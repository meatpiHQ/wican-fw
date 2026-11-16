"""TP2_0_PS through the WiCAN J2534 DLL the way an application calls it (32-bit
Python, ctypes). Run by j2534_tp20_ps_bench.py's leg L while its SAE J2819 ECU is
on the bus; alone it needs that ECU (identifier 0x201, receives on 0x740).
Run it under C:\\Users\\Ali\\idf_tmp\\py32\\python.exe -I (the DLL is 32-bit).

What it calls, in the standard's own structures:
  PassThruOpen, PassThruIoctl GET_DEVICE_INFO (input NULL, output an SPARAM_LIST,
  SAE J2534-2 section 25), PassThruConnect TP2_0_PS, SET_CONFIG J1962_PINS (an
  SCONFIG_LIST), REQUEST_CONNECTION (an SBYTE_ARRAY of 11 bytes), PassThruReadMsgs
  for CONNECTION_ESTABLISHED, PassThruWriteMsgs / PassThruReadMsgs for one request
  and its answer (each with the two length bytes a VW ECU wants in front: they are
  the application's, the device passes a message as written), a long write to an id
  without a connection
  (ERR_NO_CONNECTION_ESTABLISHED 0x00010001 and its last-error text),
  TEARDOWN_CONNECTION (an SBYTE_ARRAY of 4 bytes) and CONNECTION_LOST.

Env: WICAN_J2534_HOST/PORT (TCP) or WICAN_J2534_COM=auto, WICAN_J2534_DLL.
Prints one "OK: ..." or "BAD: ..." line per step, then DLL CHECK DONE or DLL CHECK FAIL.
"""
import ctypes
import os
import struct
import sys
import time

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
TP20 = 0x800E
SET_CONFIG, REQUEST_CONNECTION, TEARDOWN_CONNECTION, GET_DEVICE_INFO = 0x02, 0x800A, 0x800B, 0x800C
J1962_PINS = 0x8001
TP2_0_SUPPORTED, TP2_0_PS_J1962, TP2_0_SIMULTANEOUS = 0x13, 0x28, 0x46
CONNECTION_ESTABLISHED, CONNECTION_LOST = 0x00010000, 0x00020000
ERR_NO_CONNECTION_ESTABLISHED = 0x00010001
TESTER_ID, ECU_RX, TESTER_RX = 0x200, 0x740, 0x300


class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong),
                ("TxFlags", ctypes.c_ulong), ("Timestamp", ctypes.c_ulong),
                ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


class SCONFIG(ctypes.Structure):
    _fields_ = [("Parameter", ctypes.c_ulong), ("Value", ctypes.c_ulong)]


class SCONFIG_LIST(ctypes.Structure):
    _fields_ = [("NumOfParams", ctypes.c_ulong), ("ConfigPtr", ctypes.POINTER(SCONFIG))]


class SBYTE_ARRAY(ctypes.Structure):
    _fields_ = [("NumOfBytes", ctypes.c_ulong), ("BytePtr", ctypes.POINTER(ctypes.c_ubyte))]


class SPARAM(ctypes.Structure):
    _fields_ = [("Parameter", ctypes.c_ulong), ("Value", ctypes.c_ulong), ("Supported", ctypes.c_ulong)]


class SPARAM_LIST(ctypes.Structure):
    _fields_ = [("NumOfParams", ctypes.c_ulong), ("ParamPtr", ctypes.POINTER(SPARAM))]


d = ctypes.WinDLL(DLL)
d.PassThruOpen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruConnect.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
d.PassThruWriteMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruReadMsgs.argtypes = [ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
d.PassThruIoctl.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
d.PassThruDisconnect.argtypes = [ctypes.c_ulong]
d.PassThruClose.argtypes = [ctypes.c_ulong]
for f in ("PassThruOpen", "PassThruConnect", "PassThruWriteMsgs", "PassThruReadMsgs", "PassThruIoctl",
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


def hexs(data):
    return bytes(data).hex(" ").upper()


def byte_array(data):
    buf = (ctypes.c_ubyte * len(data))(*data)
    return SBYTE_ARRAY(len(data), buf), buf      # the buffer must outlive the call


def write(ch, data, flags=0):
    m = PASSTHRU_MSG()
    m.ProtocolID, m.TxFlags, m.DataSize = TP20, flags, len(data)
    ctypes.memmove(m.Data, bytes(data), len(data))
    n = ctypes.c_ulong(1)
    return d.PassThruWriteMsgs(ch, ctypes.byref(m), ctypes.byref(n), 2000) & 0xFFFFFFFF


RX = (PASSTHRU_MSG * 4)()


def read_one(ch, pred, secs):
    """The first message pred(rx_status, data) accepts within secs, or None."""
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        n = ctypes.c_ulong(1)
        st = d.PassThruReadMsgs(ch, RX, ctypes.byref(n), 100)
        if st not in (0, 0x09, 0x10) or n.value == 0:
            continue
        data = bytes(RX[0].Data[:RX[0].DataSize])
        if pred(RX[0].RxStatus, data):
            return RX[0].RxStatus, data
    return None


dev = ctypes.c_ulong(0)
st = d.PassThruOpen(None, ctypes.byref(dev))
if st != 0:
    print("DLL CHECK FAIL: PassThruOpen 0x%X (%s)" % (st, lasterr()))
    sys.exit(1)
print("dll: %s, device id %d" % (os.path.basename(DLL), dev.value), flush=True)

# the capabilities, asked the standard's way: input NULL, output an SPARAM_LIST
sp = (SPARAM * 5)((TP2_0_SUPPORTED, 0, 9), (TP2_0_SIMULTANEOUS, 0, 9), (TP2_0_PS_J1962, 0x00202000, 9),
                  (TP2_0_PS_J1962, 0x00010002, 9), (0x54, 0x1234, 9))
lst = SPARAM_LIST(5, sp)
st = d.PassThruIoctl(dev.value, GET_DEVICE_INFO, None, ctypes.byref(lst))
got = [(p.Value, p.Supported) for p in sp]
say("GET_DEVICE_INFO with an SPARAM_LIST: TP2_0_SUPPORTED and _SIMULTANEOUS 0x100, pins 6/14 supported, pins 1/2 not, "
    "an unknown parameter not supported with its value kept",
    st == 0 and got == [(0x100, 1), (0x100, 1), (0x00202000, 1), (0x00010002, 0), (0x1234, 0)],
    ("0x%X" % (st & 0xFFFFFFFF), [("0x%X" % v, s) for v, s in got], lasterr() if st else ""))
st = d.PassThruIoctl(dev.value, GET_DEVICE_INFO, None, None)
say("GET_DEVICE_INFO without a list is ERR_NULL_PARAMETER", st == 0x04, "0x%X" % (st & 0xFFFFFFFF))

ch = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, TP20, 0, 500000, ctypes.byref(ch))
if not say("PassThruConnect TP2_0_PS", st == 0, ("0x%X" % (st & 0xFFFFFFFF), lasterr() if st else "")):
    d.PassThruClose(dev.value)
    print("DLL CHECK FAIL: " + "; ".join(bad))
    sys.exit(1)
ch = ch.value

pins = SCONFIG(J1962_PINS, 0x060E)
st = d.PassThruIoctl(ch, SET_CONFIG, ctypes.byref(SCONFIG_LIST(1, ctypes.pointer(pins))), None)
say("SET_CONFIG J1962_PINS 0x060E", st == 0, "0x%X" % (st & 0xFFFFFFFF))

setup = struct.pack(">I", TESTER_ID) + bytes([0x01, 0xC0, 0x00, 0x10, TESTER_RX & 0xFF, TESTER_RX >> 8, 0x01])
arr, _keep = byte_array(setup)
st = d.PassThruIoctl(ch, REQUEST_CONNECTION, ctypes.byref(arr), None)
up = read_one(ch, lambda s, _d: s & CONNECTION_ESTABLISHED, 2.0)
say("REQUEST_CONNECTION, then CONNECTION_ESTABLISHED with the RX id 0x300 and the TX id 0x740",
    st == 0 and up is not None and up[1] == struct.pack(">II", TESTER_RX, ECU_RX),
    ("0x%X" % (st & 0xFFFFFFFF), None if up is None else hexs(up[1])))

# the two length bytes in front of a message are the application's (SAE J2534-2 19.3.1): written here, read back there
st = write(ch, struct.pack(">I", ECU_RX) + b"\x00\x02\x1A\x9B")
ans = read_one(ch, lambda s, x: not (s & (CONNECTION_ESTABLISHED | CONNECTION_LOST | 0x01)) and x[:4] == struct.pack(">I", TESTER_RX), 3.0)
want = struct.pack(">I", TESTER_RX) + b"\x00\x2A\x5A\x9B" + bytes((i * 3 + 1) & 0xFF for i in range(40))
say("00 02 1A 9B written, the answer read as one message behind the RX id: its length bytes 00 2A, then the 42 bytes",
    st == 0 and ans is not None and ans[1] == want, ("0x%X" % st, None if ans is None else hexs(ans[1][:12])))

st = write(ch, struct.pack(">I", 0x7AA) + bytes(range(9)))
say("a 9-byte message to an id without a connection returns ERR_NO_CONNECTION_ESTABLISHED 0x00010001",
    st == ERR_NO_CONNECTION_ESTABLISHED, "0x%X" % st)
say("the last-error text names it", "ERR_NO_CONNECTION_ESTABLISHED" in lasterr(), lasterr())

arr, _keep = byte_array(struct.pack(">I", TESTER_RX))
st = d.PassThruIoctl(ch, TEARDOWN_CONNECTION, ctypes.byref(arr), None)
lost = read_one(ch, lambda s, _d: s & CONNECTION_LOST, 2.0)
say("TEARDOWN_CONNECTION, then CONNECTION_LOST with the RX id 0x300 and reason 0",
    st == 0 and lost is not None and lost[1] == struct.pack(">I", TESTER_RX) + b"\x00",
    ("0x%X" % (st & 0xFFFFFFFF), None if lost is None else hexs(lost[1])))

d.PassThruDisconnect(ch)
d.PassThruClose(dev.value)
if bad:
    print("DLL CHECK FAIL: " + "; ".join(bad), flush=True)
    sys.exit(1)
print("DLL CHECK DONE", flush=True)
