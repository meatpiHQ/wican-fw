#!/usr/bin/env python3
"""The WiCAN J2534 DLL's return codes and its ERR_BUFFER_OVERFLOW  ->  DLL SESSION CHECK DONE

What an application sees through the driver, called with ctypes under the 32-bit Python
(the DLL is 32-bit). Two ways to run it:

  --fake           against fake devices on this PC (no unit, no bench): a device of wire
                   version 1 (every firmware before 2026-11-18: its status numbers from
                   0x14 to 0x1A are the old ones) and one of version 2 (SAE J2534-1's
                   numbers). Driver 1.10 must hand the application SAE's numbers from
                   both, say "in use" as ERR_DEVICE_IN_USE (0x0E), and return
                   ERR_BUFFER_OVERFLOW from PassThruReadMsgs for the first message after a
                   loss: one the device reported (EVENT RX_LOST) and one in its own ring
                   (300 messages while the application does not read).
  --unit H:P       against the unit (its J2534 port, through the bench's tunnel): the
                   refusals of a real firmware through the DLL. With --old the numbers a
                   driver before 1.10 returns (they must be what they always were).

usage:  python j2534_session_dll_check.py --py32 <32-bit python> --dll <wican_j2534.dll>
                                          (--fake | --unit 127.0.0.1:18534 [--old])
Prints one "OK: ..." or "BAD: ..." line per step, then DLL SESSION CHECK DONE or FAIL.
"""
import argparse
import ctypes
import os
import socket
import struct
import subprocess
import sys
import threading
import time

HDR = struct.Struct("<HBBHHI")
MSGH = struct.Struct("<IIIIII")             # protocol, rx_status, tx_flags, timestamp, extra, size
MAGIC = 0x4A35
HELLO, OPEN, CLOSE, CONNECT, DISCONNECT, WRITE_MSGS, STOP_FILTER = 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x12
ACK, RX_MSG, EVENT = 0x80, 0x81, 0x82
CAN, ISO15765 = 5, 6
ISO15765_FRAME_PAD = 0x40
# name: (wire version 1, SAE J2534-1)
NUMBERS = {"ERR_MSG_PROTOCOL_ID": (0x14, 0x15), "ERR_INVALID_FILTER_ID": (0x15, 0x16), "ERR_NO_FLOW_CONTROL": (0x16, 0x17),
           "ERR_NOT_UNIQUE": (0x17, 0x18), "ERR_INVALID_BAUDRATE": (0x18, 0x19), "ERR_INVALID_DEVICE_ID": (0x19, 0x1A),
           "ERR_DEVICE_IN_USE": (0x1A, 0x0E)}
SAME = {"ERR_FAILED": 0x07, "ERR_TIMEOUT": 0x09, "ERR_EXCEEDED_LIMIT": 0x0C, "ERR_NO_CONNECTION_ESTABLISHED": 0x10001}
ERR_BUFFER_EMPTY, ERR_BUFFER_OVERFLOW = 0x10, 0x12
MARK = 0x7A1                                # the id of the messages a fake device sends
SCRIPT = 0xF0F0F000                         # a write of this "status" + n makes the fake device run its script n

bad = []


def say(name, ok, detail=""):
    print(("OK: " if ok else "BAD: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    if not ok:
        bad.append(name)
    return ok


# ---- the fake device (the parent's side) ------------------------------------------------

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("the DLL closed the link")
        buf += chunk
    return buf


def rx_msg(channel, counter):
    data = struct.pack(">II", MARK, counter)
    return HDR.pack(MAGIC, 1, RX_MSG, 0, channel, MSGH.size + len(data)) + MSGH.pack(CAN, 0, 0, counter, 0, len(data)) + data


class Fake(threading.Thread):
    """One session of a device. version: what HELLO answers. refuse: the status HELLO is refused with.
    A WRITE_MSGS is answered with the status in the first message's bytes behind its id (4 bytes,
    big-endian); the status SCRIPT + n runs script n instead and answers NOERROR."""

    def __init__(self, version=1, refuse=None):
        super().__init__(daemon=True)
        self.version, self.refuse = version, refuse
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(20)
        self.port = self.listener.getsockname()[1]
        self.hello = None           # the HELLO payload the DLL sent
        self.errors = []

    def script(self, conn, n, ch):
        if n == 1:      # two messages, a loss of five the device reports, two more
            conn.sendall(rx_msg(ch, 1) + rx_msg(ch, 2) + HDR.pack(MAGIC, 1, EVENT, 0, ch, 8) + struct.pack("<II", 1, 5)
                         + rx_msg(ch, 8) + rx_msg(ch, 9))
        elif n == 2:    # more than the DLL's ring holds
            conn.sendall(b"".join(rx_msg(ch, i) for i in range(300)))
        elif n == 3:    # the first messages after that
            conn.sendall(b"".join(rx_msg(ch, i) for i in range(300, 303)))

    def run(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.settimeout(15)
                while True:
                    _m, _v, kind, seq, ch, size = HDR.unpack(recv_exact(conn, HDR.size))
                    payload = recv_exact(conn, size)
                    body = struct.pack("<I", 0)
                    after = None
                    if kind == HELLO:
                        self.hello = payload
                        body = struct.pack("<I", self.refuse) if self.refuse is not None else struct.pack("<II", 0, self.version)
                    elif kind in (OPEN, CONNECT):
                        body, ch = struct.pack("<II", 0, 1), (1 if kind == CONNECT else ch)
                    elif kind == WRITE_MSGS and len(payload) >= 4 + MSGH.size + 8:
                        st = struct.unpack_from(">I", payload, 4 + MSGH.size + 4)[0]
                        if st & 0xFFFFFF00 == SCRIPT:
                            after = st & 0xFF
                        else:
                            body = struct.pack("<I", st)
                    conn.sendall(HDR.pack(MAGIC, 1, ACK, seq, ch, len(body)) + body)
                    if after is not None:
                        self.script(conn, after, ch)
                    if kind == CLOSE or self.refuse is not None:
                        break
        except (OSError, EOFError, ValueError) as e:
            self.errors.append("%s: %s" % (type(e).__name__, e))
        finally:
            self.listener.close()


def child(a, scenario, host, port, extra=()):
    env = dict(os.environ, WICAN_J2534_HOST=host, WICAN_J2534_PORT=str(port), WICAN_J2534_DLL=a.dll, WICAN_J2534_LOG="off")
    env.pop("WICAN_J2534_COM", None)
    p = subprocess.run([a.py32, "-I", os.path.abspath(__file__), "--child", scenario] + list(extra),
                       env=env, capture_output=True, text=True, timeout=90)
    out = (p.stdout + p.stderr).rstrip()
    for ln in out.splitlines():
        print(ln, flush=True)
        if ln.startswith("BAD: "):
            bad.append(ln[5:])
    if p.returncode != 0 and "BAD: " not in out:
        bad.append("the %s steps did not run to the end (exit %d)" % (scenario, p.returncode))


def parent(a):
    if a.unit:
        host, port = a.unit.rsplit(":", 1)
        print("-- the unit at %s, %s" % (a.unit, "a driver before 1.10" if a.old else "driver 1.10 or later"), flush=True)
        child(a, "unit-old" if a.old else "unit", host if host != "localhost" else "127.0.0.1", port)
    else:
        for label, scenario, kw in (
                ("a device of wire version 1 (a firmware before 2026-11-18)", "codes-v1", {"version": 1}),
                ("a device of wire version 2", "codes-v2", {"version": 2}),
                ("a version 1 device that is in use (it refuses HELLO with 0x1A)", "in-use", {"refuse": 0x1A}),
                ("a version 2 device that is in use (it refuses HELLO with 0x0E)", "in-use", {"refuse": 0x0E}),
                ("messages lost in the device (EVENT RX_LOST)", "lost-device", {"version": 2}),
                ("messages lost in the DLL (the application does not read)", "lost-ring", {"version": 2})):
            print("-- %s" % label, flush=True)
            f = Fake(**kw)
            f.start()
            child(a, scenario, "127.0.0.1", f.port)
            f.join(20)
            say("the DLL asked for wire version 2 in its HELLO", f.hello == struct.pack("<I", 2), None if f.hello is None else f.hello.hex())
            if f.errors and scenario != "in-use":
                say("the fake device ran to the end", False, f.errors)
    if bad:
        print("DLL SESSION CHECK FAIL: " + "; ".join(bad), flush=True)
        sys.exit(1)
    print("DLL SESSION CHECK DONE", flush=True)


# ---- the DLL's side (32-bit Python) ------------------------------------------------------

class PASSTHRU_MSG(ctypes.Structure):
    _fields_ = [("ProtocolID", ctypes.c_ulong), ("RxStatus", ctypes.c_ulong),
                ("TxFlags", ctypes.c_ulong), ("Timestamp", ctypes.c_ulong),
                ("DataSize", ctypes.c_ulong), ("ExtraDataIndex", ctypes.c_ulong),
                ("Data", ctypes.c_ubyte * 4128)]


def load():
    d = ctypes.WinDLL(os.environ["WICAN_J2534_DLL"])
    u, pu = ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)
    pm = ctypes.POINTER(PASSTHRU_MSG)
    d.PassThruOpen.argtypes = [ctypes.c_void_p, pu]
    d.PassThruClose.argtypes = [u]
    d.PassThruConnect.argtypes = [u, u, u, u, pu]
    d.PassThruDisconnect.argtypes = [u]
    d.PassThruWriteMsgs.argtypes = [u, pm, pu, u]
    d.PassThruReadMsgs.argtypes = [u, pm, pu, u]
    d.PassThruStopMsgFilter.argtypes = [u, u]
    d.PassThruGetLastError.argtypes = [ctypes.c_char_p]
    d.PassThruReadVersion.argtypes = [u, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    for f in ("PassThruOpen", "PassThruClose", "PassThruConnect", "PassThruDisconnect", "PassThruWriteMsgs", "PassThruReadMsgs",
              "PassThruStopMsgFilter", "PassThruGetLastError", "PassThruReadVersion"):
        getattr(d, f).restype = ctypes.c_long
    return d


def lasterr(d):
    buf = ctypes.create_string_buffer(256)
    d.PassThruGetLastError(buf)
    return buf.value.decode(errors="replace")


def msg(proto, data, flags=0):
    m = PASSTHRU_MSG()
    m.ProtocolID, m.TxFlags, m.DataSize = proto, flags, len(data)
    ctypes.memmove(m.Data, bytes(data), len(data))
    return m


def write(d, ch, proto, data, flags=0):
    n = ctypes.c_ulong(1)
    return d.PassThruWriteMsgs(ch, ctypes.byref(msg(proto, data, flags)), ctypes.byref(n), 2000) & 0xFFFFFFFF


def read(d, ch, want, timeout=200):
    """-> (the return value, the counters of the messages handed over)."""
    arr = (PASSTHRU_MSG * want)()
    n = ctypes.c_ulong(want)
    st = d.PassThruReadMsgs(ch, arr, ctypes.byref(n), timeout) & 0xFFFFFFFF
    return st, [struct.unpack(">I", bytes(arr[i].Data[4:8]))[0] for i in range(n.value)]


def opened(d):
    dev, ch = ctypes.c_ulong(0), ctypes.c_ulong(0)
    st = d.PassThruOpen(None, ctypes.byref(dev)) & 0xFFFFFFFF
    if not say("PassThruOpen", st == 0, ("0x%X" % st, lasterr(d) if st else "")):
        return None, None
    st = d.PassThruConnect(dev.value, ISO15765, 0, 500000, ctypes.byref(ch)) & 0xFFFFFFFF
    say("PassThruConnect ISO15765", st == 0, ("0x%X" % st, lasterr(d) if st else ""))
    return dev.value, ch.value


def child_codes(d, col):
    """Every status a device refuses with, written by a fake device, as the application gets it."""
    dev, ch = opened(d)
    if dev is None:
        return
    for name, pair in sorted(NUMBERS.items(), key=lambda kv: kv[1][0]):
        sent, want = pair[col], pair[1]
        st = write(d, ch, ISO15765, struct.pack(">II", 0x7E0, sent))
        text = lasterr(d)
        say("the device's 0x%02X is %s 0x%02X for the application, and the last-error text names it" % (sent, name, want),
            st == want and name in text, ("0x%X" % st, text[:70]))
    for name, code in sorted(SAME.items(), key=lambda kv: kv[1]):
        st = write(d, ch, ISO15765, struct.pack(">II", 0x7E0, code))
        say("%s stays 0x%X" % (name, code), st == code, "0x%X" % st)
    st = write(d, ch, ISO15765, struct.pack(">II", 0x7E0, 0))
    say("a write the device accepts is STATUS_NOERROR", st == 0, "0x%X" % st)
    d.PassThruDisconnect(ch)
    d.PassThruClose(dev)


def child_in_use(d):
    dev = ctypes.c_ulong(0)
    st = d.PassThruOpen(None, ctypes.byref(dev)) & 0xFFFFFFFF
    text = lasterr(d)
    say("PassThruOpen on a device another tester holds returns ERR_DEVICE_IN_USE 0x0E and says so",
        st == 0x0E and "ERR_DEVICE_IN_USE" in text, ("0x%X" % st, text[:70]))


def child_lost_device(d):
    dev, ch = opened(d)
    if dev is None:
        return
    say("the write that starts the device's script", write(d, ch, ISO15765, struct.pack(">II", 0x7E0, SCRIPT + 1)) == 0)
    time.sleep(0.4)
    st, got = read(d, ch, 10)
    say("the first read returns the two messages in front of the loss, STATUS_NOERROR", st == 0 and got == [1, 2], ("0x%X" % st, got))
    st, got = read(d, ch, 10)
    text = lasterr(d)
    say("the next read returns ERR_BUFFER_OVERFLOW 0x12 WITH the messages behind the loss, the first of them first",
        st == ERR_BUFFER_OVERFLOW and got == [8, 9], ("0x%X" % st, got))
    say("the last-error text names it", "ERR_BUFFER_OVERFLOW" in text, text[:70])
    st, got = read(d, ch, 10, 50)
    say("then the queue is empty: ERR_BUFFER_EMPTY, the overflow is reported once", st == ERR_BUFFER_EMPTY and got == [], ("0x%X" % st, got))
    d.PassThruDisconnect(ch)
    d.PassThruClose(dev)


def child_lost_ring(d):
    dev, ch = opened(d)
    if dev is None:
        return
    say("the write that makes the device send 300 messages", write(d, ch, ISO15765, struct.pack(">II", 0x7E0, SCRIPT + 2)) == 0)
    time.sleep(0.8)             # the application does not read: the DLL's ring takes 255
    got, sts = [], []
    while True:
        st, part = read(d, ch, 100, 50)
        if not part:
            break
        sts.append(st)
        got += part
    say("what the ring held is read in order, STATUS_NOERROR: nothing is missing in front of any of it",
        got == list(range(len(got))) and 200 <= len(got) < 300 and set(sts) == {0}, (len(got), ["0x%X" % s for s in sts]))
    say("the write that makes the device send three more", write(d, ch, ISO15765, struct.pack(">II", 0x7E0, SCRIPT + 3)) == 0)
    time.sleep(0.4)
    st, part = read(d, ch, 10)
    say("the first message after the %d that were lost comes with ERR_BUFFER_OVERFLOW 0x12" % (300 - len(got)),
        st == ERR_BUFFER_OVERFLOW and part == [300, 301, 302], ("0x%X" % st, part))
    d.PassThruDisconnect(ch)
    d.PassThruClose(dev)


def child_unit(d, old):
    col = 0 if old else 1

    def num(name):
        return NUMBERS[name][col]

    dev, ch = ctypes.c_ulong(0), ctypes.c_ulong(0)
    st = d.PassThruOpen(None, ctypes.byref(dev)) & 0xFFFFFFFF
    if not say("PassThruOpen", st == 0, ("0x%X" % st, lasterr(d) if st else "")):
        return
    fw, dl, api = (ctypes.create_string_buffer(80) for _ in range(3))
    d.PassThruReadVersion(dev.value, fw, dl, api)
    print("dll: %s, api %s" % (dl.value.decode(), api.value.decode()), flush=True)
    st = d.PassThruOpen(None, ctypes.byref(ctypes.c_ulong(0))) & 0xFFFFFFFF
    say("a second PassThruOpen returns ERR_DEVICE_IN_USE 0x%02X" % num("ERR_DEVICE_IN_USE"), st == num("ERR_DEVICE_IN_USE"), "0x%X" % st)
    st = d.PassThruConnect(dev.value, CAN, 0, 250000, ctypes.byref(ch)) & 0xFFFFFFFF
    text = lasterr(d)
    say("PassThruConnect at 250000 on a bus at 500000 returns ERR_INVALID_BAUDRATE 0x%02X, the text names it" % num("ERR_INVALID_BAUDRATE"),
        st == num("ERR_INVALID_BAUDRATE") and "ERR_INVALID_BAUDRATE" in text, ("0x%X" % st, text[:60]))
    st = d.PassThruConnect(dev.value, ISO15765, 0, 500000, ctypes.byref(ch)) & 0xFFFFFFFF
    say("PassThruConnect ISO15765 at 500000", st == 0, "0x%X" % st)
    st = write(d, ch.value, ISO15765, struct.pack(">I", 0x6F5) + bytes(20), ISO15765_FRAME_PAD)
    text = lasterr(d)
    say("a 20-byte message to an id without a flow control filter returns ERR_NO_FLOW_CONTROL 0x%02X, the text names it"
        % num("ERR_NO_FLOW_CONTROL"), st == num("ERR_NO_FLOW_CONTROL") and "ERR_NO_FLOW_CONTROL" in text, ("0x%X" % st, text[:60]))
    st = d.PassThruStopMsgFilter(ch.value, 77) & 0xFFFFFFFF
    text = lasterr(d)
    say("PassThruStopMsgFilter of a filter that is not there returns ERR_INVALID_FILTER_ID 0x%02X, the text names it"
        % num("ERR_INVALID_FILTER_ID"), st == num("ERR_INVALID_FILTER_ID") and "ERR_INVALID_FILTER_ID" in text, ("0x%X" % st, text[:60]))
    say("PassThruDisconnect, PassThruClose", (d.PassThruDisconnect(ch.value), d.PassThruClose(dev.value)) == (0, 0))
    st = d.PassThruClose(dev.value) & 0xFFFFFFFF
    say("PassThruClose again returns ERR_INVALID_DEVICE_ID 0x%02X" % num("ERR_INVALID_DEVICE_ID"), st == num("ERR_INVALID_DEVICE_ID"),
        "0x%X" % st)


def run_child(scenario):
    d = load()
    if scenario == "codes-v1":
        child_codes(d, 0)
    elif scenario == "codes-v2":
        child_codes(d, 1)
    elif scenario == "in-use":
        child_in_use(d)
    elif scenario == "lost-device":
        child_lost_device(d)
    elif scenario == "lost-ring":
        child_lost_ring(d)
    elif scenario in ("unit", "unit-old"):
        child_unit(d, scenario == "unit-old")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--py32", default=r"C:\Users\Ali\idf_tmp\py32\python.exe")
    ap.add_argument("--dll", default=os.environ.get("WICAN_J2534_DLL"))
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--unit", default=None)
    ap.add_argument("--old", action="store_true")
    ap.add_argument("--child", default=None)
    a = ap.parse_args()
    if a.child:
        run_child(a.child)
    if not a.dll or not os.path.exists(a.dll) or not os.path.exists(a.py32):
        print("DLL SESSION CHECK FAIL: the DLL or the 32-bit Python is not there (%s, %s)" % (a.dll, a.py32))
        sys.exit(1)
    if not a.fake and not a.unit:
        ap.error("one of --fake and --unit")
    parent(a)


if __name__ == "__main__":
    main()
