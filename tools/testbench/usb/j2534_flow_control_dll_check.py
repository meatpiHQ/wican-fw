"""ISO15765_BS and ISO15765_STMIN through the WiCAN J2534 DLL the way an application
sets them (32-bit Python, ctypes). Run by j2534_flow_control_bench.py's leg D while its
ISO 15765-2 ECU is on the bus; alone it needs that ECU (request id 0x6F2, answers on
0x6FA: 22 hh ll ss is answered by 0xhhll bytes, 62 hh ll ss then a pattern).
Run it under C:\\Users\\Ali\\idf_tmp\\py32\\python.exe -I (the DLL is 32-bit).

What it calls, in the standard's own structures:
  PassThruOpen, PassThruConnect ISO15765, PassThruIoctl GET_CONFIG and SET_CONFIG with
  SCONFIG_LISTs (the two values beside DATA_RATE and LOOPBACK, the way udsoncan sends
  them), PassThruStartMsgFilter FLOW_CONTROL, PassThruWriteMsgs / PassThruReadMsgs for
  a 20-byte, a 100-byte and a 4095-byte answer, a value out of range
  (ERR_INVALID_IOCTL_VALUE and its last-error text).

The flow controls themselves are the bench's to check: it reads them off the bus.

Env: WICAN_J2534_HOST/PORT (TCP) or WICAN_J2534_COM=auto, WICAN_J2534_DLL, WICAN_FC_REQ.
Prints one "OK: ..." or "BAD: ..." line per step, then DLL CHECK DONE or DLL CHECK FAIL.
"""
import ctypes
import os
import struct
import sys
import time

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
REQ = int(os.environ.get("WICAN_FC_REQ", "6F2"), 16)
RSP = REQ + 8
ISO15765 = 6
GET_CONFIG, SET_CONFIG = 0x01, 0x02
DATA_RATE, LOOPBACK, ISO15765_BS, ISO15765_STMIN = 0x01, 0x03, 0x1E, 0x1F
FLOW_CONTROL_FILTER, ISO15765_FRAME_PAD = 3, 0x40
ERR_INVALID_IOCTL_VALUE = 0x05
NOT_A_MESSAGE = 0x0B            # TX_MSG_TYPE, START_OF_MESSAGE, TX_INDICATION


class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong),
                ("TxFlags", ctypes.c_ulong), ("Timestamp", ctypes.c_ulong),
                ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


class SCONFIG(ctypes.Structure):
    _fields_ = [("Parameter", ctypes.c_ulong), ("Value", ctypes.c_ulong)]


class SCONFIG_LIST(ctypes.Structure):
    _fields_ = [("NumOfParams", ctypes.c_ulong), ("ConfigPtr", ctypes.POINTER(SCONFIG))]


d = ctypes.WinDLL(DLL)
d.PassThruOpen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruConnect.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruWriteMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruReadMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruStartMsgFilter.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(PASSTHRU_MSG),
                                     ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong)]
d.PassThruIoctl.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
d.PassThruDisconnect.argtypes = [ctypes.c_ulong]
d.PassThruClose.argtypes = [ctypes.c_ulong]
for f in ("PassThruOpen", "PassThruConnect", "PassThruWriteMsgs", "PassThruReadMsgs", "PassThruStartMsgFilter", "PassThruIoctl",
          "PassThruGetLastError", "PassThruDisconnect", "PassThruClose"):
    getattr(d, f).restype = ctypes.c_long

bad = []


def say(name, ok, detail=""):
    print(("OK: " if ok else "BAD: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    if not ok:
        bad.append(name)
    return ok


def lasterr():
    buf = ctypes.create_string_buffer(256)
    d.PassThruGetLastError(buf)
    return buf.value.decode(errors="replace")


def iso(data, flags=ISO15765_FRAME_PAD):
    m = PASSTHRU_MSG()
    m.ProtocolID, m.TxFlags, m.DataSize = ISO15765, flags, len(data)
    ctypes.memmove(m.Data, bytes(data), len(data))
    return m


def config(ch, ioctl_id, pairs):
    """SET_CONFIG or GET_CONFIG with an SCONFIG_LIST -> (status, [values as the DLL left them])."""
    arr = (SCONFIG * len(pairs))(*pairs)
    st = d.PassThruIoctl(ch, ioctl_id, ctypes.byref(SCONFIG_LIST(len(pairs), arr)), None) & 0xFFFFFFFF
    return st, [c.Value for c in arr]


def pattern(seed, n):
    return bytes((seed * 13 + i * 7 + (i >> 8)) & 0xFF for i in range(n))


RX = (PASSTHRU_MSG * 2)()


def ask(ch, total, seed, secs):
    """22 hh ll ss written, the answer read. Returns (the write's status, whether it came whole and right)."""
    req = b"\x22" + struct.pack(">H", total) + bytes([seed])
    n = ctypes.c_ulong(1)
    st = d.PassThruWriteMsgs(ch, ctypes.byref(iso(struct.pack(">I", REQ) + req)), ctypes.byref(n), 2000) & 0xFFFFFFFF
    want = struct.pack(">I", RSP) + b"\x62" + req[1:4] + pattern(seed, total - 4)
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        n = ctypes.c_ulong(1)
        rs = d.PassThruReadMsgs(ch, RX, ctypes.byref(n), 100)
        if rs not in (0, 0x09, 0x10) or n.value == 0 or RX[0].RxStatus & NOT_A_MESSAGE:
            continue
        return st, bytes(RX[0].Data[:RX[0].DataSize]) == want
    return st, False


dev = ctypes.c_ulong(0)
st = d.PassThruOpen(None, ctypes.byref(dev))
if st != 0:
    print("DLL CHECK FAIL: PassThruOpen 0x%X (%s)" % (st, lasterr()))
    sys.exit(1)
print("dll: %s, device id %d, ECU %03X / %03X" % (os.path.basename(DLL), dev.value, REQ, RSP), flush=True)

ch = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, ISO15765, 0, 500000, ctypes.byref(ch))
if not say("PassThruConnect ISO15765", st == 0, ("0x%X" % (st & 0xFFFFFFFF), lasterr() if st else "")):
    d.PassThruClose(dev.value)
    print("DLL CHECK FAIL: " + "; ".join(bad))
    sys.exit(1)
ch = ch.value

st, vals = config(ch, GET_CONFIG, [(ISO15765_BS, 0xEE), (ISO15765_STMIN, 0xEE)])
say("GET_CONFIG on a new channel reads ISO15765_BS 8 and ISO15765_STMIN 1", st == 0 and vals == [8, 1], ("0x%X" % st, vals))

fid = ctypes.c_ulong(0)
st = d.PassThruStartMsgFilter(ch, FLOW_CONTROL_FILTER, ctypes.byref(iso(b"\xFF\xFF\xFF\xFF")), ctypes.byref(iso(struct.pack(">I", RSP))),
                              ctypes.byref(iso(struct.pack(">I", REQ))), ctypes.byref(fid)) & 0xFFFFFFFF
say("PassThruStartMsgFilter FLOW_CONTROL %03X / %03X" % (REQ, RSP), st == 0, ("0x%X" % st, lasterr() if st else ""))

st, whole = ask(ch, 20, 0x31, 3.0)
say("a 20-byte answer read whole with the values a channel opens with", st == 0 and whole, "0x%X" % st)

# the list udsoncan's J2534 connection sends, with other values for the two
st, _v = config(ch, SET_CONFIG, [(DATA_RATE, 500000), (LOOPBACK, 0), (ISO15765_BS, 4), (ISO15765_STMIN, 2)])
say("SET_CONFIG DATA_RATE, LOOPBACK, ISO15765_BS 4, ISO15765_STMIN 2", st == 0, ("0x%X" % st, lasterr() if st else ""))
st, vals = config(ch, GET_CONFIG, [(ISO15765_BS, 0xEE), (ISO15765_STMIN, 0xEE), (DATA_RATE, 0)])
say("GET_CONFIG reads 4 and 2 back from the device, and DATA_RATE 500000", st == 0 and vals == [4, 2, 500000], ("0x%X" % st, vals))
st, whole = ask(ch, 100, 0x32, 4.0)
say("a 100-byte answer read whole", st == 0 and whole, "0x%X" % st)

st, _v = config(ch, SET_CONFIG, [(ISO15765_BS, 0x100)])
say("ISO15765_BS 0x100 returns ERR_INVALID_IOCTL_VALUE", st == ERR_INVALID_IOCTL_VALUE, "0x%X" % st)
say("the last-error text names it", "ERR_INVALID_IOCTL_VALUE" in lasterr(), lasterr())
st, vals = config(ch, GET_CONFIG, [(ISO15765_BS, 0xEE), (ISO15765_STMIN, 0xEE)])
say("the refused value changed nothing: still 4 and 2", st == 0 and vals == [4, 2], ("0x%X" % st, vals))

st, _v = config(ch, SET_CONFIG, [(ISO15765_BS, 0), (ISO15765_STMIN, 0xF2)])
st2, vals = config(ch, GET_CONFIG, [(ISO15765_STMIN, 0xEE), (ISO15765_BS, 0xEE)])
say("ISO15765_BS 0 and ISO15765_STMIN 0xF2 (200 us) set and read back", st == 0 and st2 == 0 and vals == [0xF2, 0], ("0x%X" % st, vals))
st, whole = ask(ch, 4095, 0x33, 10.0)
say("a 4095-byte answer read whole, every byte", st == 0 and whole, "0x%X" % st)

d.PassThruDisconnect(ch)
d.PassThruClose(dev.value)
if bad:
    print("DLL CHECK FAIL: " + "; ".join(bad), flush=True)
    sys.exit(1)
print("DLL CHECK DONE", flush=True)
