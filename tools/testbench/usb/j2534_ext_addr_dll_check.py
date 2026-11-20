"""ISO 15765 extended addressing through the WiCAN J2534 DLL the way an application uses it
(32-bit Python, ctypes). Run by j2534_ext_addr_bench.py's leg D while its gateway is on the
bus; alone it needs that gateway (an ECU that takes frames led by WICAN_EXT_TX_AE on
WICAN_EXT_REQ and answers led by WICAN_EXT_RX_AE on WICAN_EXT_RSP: 22 hh ll ss is answered
by 0xhhll bytes, anything else by its bytes back with the first one + 0x40).
Run it under C:\\Users\\Ali\\idf_tmp\\py32\\python.exe -I (the DLL is 32-bit).

What it calls, in the standard's own structures:
  PassThruOpen, PassThruConnect ISO15765, PassThruStartMsgFilter FLOW_CONTROL with
  ISO15765_ADDR_TYPE (mask, pattern and flow-control message of five bytes), then
  PassThruWriteMsgs / PassThruReadMsgs with [id][address extension][payload]: a single
  frame (its TxDone and its answer), a 100-byte answer (START_OF_MESSAGE in front of it),
  a 300-byte request with its 300 bytes back.

The frames themselves are the bench's to check: it reads them off the bus.

Env: WICAN_J2534_HOST/PORT (TCP) or WICAN_J2534_COM=auto, WICAN_J2534_DLL, WICAN_EXT_REQ,
WICAN_EXT_RSP, WICAN_EXT_TX_AE, WICAN_EXT_RX_AE.
Prints one "OK: ..." or "BAD: ..." line per step, then DLL CHECK DONE or DLL CHECK FAIL.
"""
import ctypes
import os
import struct
import sys
import time

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
REQ = int(os.environ.get("WICAN_EXT_REQ", "6F5"), 16)
RSP = int(os.environ.get("WICAN_EXT_RSP", "6FD"), 16)
TX_AE = int(os.environ.get("WICAN_EXT_TX_AE", "41"), 16)
RX_AE = int(os.environ.get("WICAN_EXT_RX_AE", "E9"), 16)
ISO15765 = 6
FLOW_CONTROL_FILTER = 3
ISO15765_FRAME_PAD, ISO15765_ADDR_TYPE = 0x40, 0x80
TX_MSG_TYPE, START_OF_MESSAGE, TX_INDICATION = 0x01, 0x02, 0x08
FLAGS = ISO15765_FRAME_PAD | ISO15765_ADDR_TYPE
TO_ECU = struct.pack(">I", REQ) + bytes([TX_AE])        # what a message to the ECU begins with
FROM_ECU = struct.pack(">I", RSP) + bytes([RX_AE])      # what a message from it begins with


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
d.PassThruStartMsgFilter.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(PASSTHRU_MSG),
                                     ctypes.POINTER(PASSTHRU_MSG), ctypes.POINTER(ctypes.c_ulong)]
d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
d.PassThruDisconnect.argtypes = [ctypes.c_ulong]
d.PassThruClose.argtypes = [ctypes.c_ulong]
for f in ("PassThruOpen", "PassThruConnect", "PassThruWriteMsgs", "PassThruReadMsgs", "PassThruStartMsgFilter",
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


def iso(data, flags=FLAGS):
    m = PASSTHRU_MSG()
    m.ProtocolID, m.TxFlags, m.DataSize = ISO15765, flags, len(data)
    ctypes.memmove(m.Data, bytes(data), len(data))
    return m


def pattern(seed, n):
    return bytes((seed * 13 + i * 7 + (i >> 8)) & 0xFF for i in range(n))


RX = (PASSTHRU_MSG * 1)()


def write(payload):
    n = ctypes.c_ulong(1)
    return d.PassThruWriteMsgs(ch, ctypes.byref(iso(TO_ECU + payload)), ctypes.byref(n), 5000) & 0xFFFFFFFF


def read(secs):
    """The next message or indication -> (RxStatus, its data), or None after secs."""
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        n = ctypes.c_ulong(1)
        rs = d.PassThruReadMsgs(ch, RX, ctypes.byref(n), 100) & 0xFFFFFFFF
        if rs == 0 and n.value == 1:
            return RX[0].RxStatus, bytes(RX[0].Data[:RX[0].DataSize])
    return None


def hx(r):
    return None if r is None else ("0x%X" % r[0], r[1][:16].hex(" ").upper() + (" ..." if len(r[1]) > 16 else ""), len(r[1]))


dev = ctypes.c_ulong(0)
st = d.PassThruOpen(None, ctypes.byref(dev))
if st != 0:
    print("DLL CHECK FAIL: PassThruOpen 0x%X (%s)" % (st, lasterr()))
    sys.exit(1)
print("dll: %s, device id %d, ECU %03X + %02X / %03X + %02X" % (os.path.basename(DLL), dev.value, REQ, TX_AE, RSP, RX_AE), flush=True)

ch = ctypes.c_ulong(0)
st = d.PassThruConnect(dev.value, ISO15765, 0, 500000, ctypes.byref(ch))
if not say("PassThruConnect ISO15765", st == 0, ("0x%X" % (st & 0xFFFFFFFF), lasterr() if st else "")):
    d.PassThruClose(dev.value)
    print("DLL CHECK FAIL: " + "; ".join(bad))
    sys.exit(1)
ch = ch.value

fid = ctypes.c_ulong(0)
st = d.PassThruStartMsgFilter(ch, FLOW_CONTROL_FILTER, ctypes.byref(iso(b"\xFF" * 5)), ctypes.byref(iso(FROM_ECU)),
                              ctypes.byref(iso(TO_ECU)), ctypes.byref(fid)) & 0xFFFFFFFF
say("PassThruStartMsgFilter FLOW_CONTROL with ISO15765_ADDR_TYPE: five bytes each", st == 0, ("0x%X" % st, lasterr() if st else ""))

st = write(b"\x10\x03")
done, ans = read(2.0), read(3.0)
say("a single-frame message written: its TxDone carries the id and the address extension, RxStatus 0x89",
    st == 0 and done == (TX_MSG_TYPE | TX_INDICATION | ISO15765_ADDR_TYPE, TO_ECU), ("0x%X" % st, hx(done)))
say("its answer: [id][address extension] 50 03, RxStatus 0x80", ans == (ISO15765_ADDR_TYPE, FROM_ECU + b"\x50\x03"), hx(ans))

st = write(b"\x22\x00\x64\x61")
done, som, ans = read(2.0), read(3.0), read(4.0)
say("a 100-byte answer: START_OF_MESSAGE (0x82, the id and the address extension) in front of it, then every byte",
    st == 0 and done is not None and som == (START_OF_MESSAGE | ISO15765_ADDR_TYPE, FROM_ECU)
    and ans == (ISO15765_ADDR_TYPE, FROM_ECU + b"\x62\x00\x64\x61" + pattern(0x61, 96)), ("0x%X" % st, hx(som), hx(ans)))

req = b"\x3E" + pattern(0x62, 299)
st = write(req)
done, som, ans = read(3.0), read(3.0), read(5.0)
say("a 300-byte request written (51 frames) and its 300 bytes read back",
    st == 0 and done == (TX_MSG_TYPE | TX_INDICATION | ISO15765_ADDR_TYPE, TO_ECU) and som is not None
    and ans == (ISO15765_ADDR_TYPE, FROM_ECU + b"\x7E" + req[1:]), ("0x%X" % st, hx(done), hx(ans)))

d.PassThruDisconnect(ch)
d.PassThruClose(dev.value)
if bad:
    print("DLL CHECK FAIL: " + "; ".join(bad), flush=True)
    sys.exit(1)
print("DLL CHECK DONE", flush=True)
