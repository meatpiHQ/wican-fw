"""The TP 2.0-shaped block transfer (the tool side of j2534_tp20_responder.py)
driven straight into a Tactrix Openport 2.0 over WinUSB, with the device's own
ASCII protocol (learned from the OPTA driver's log, 2026-11-05):

  ati\\r\\n                     -> ari <text>\\r\\n          identify
  ata\\r\\n                     -> aro\\r\\n                 (open)
  ato5 0 500000 0\\r\\n         -> aro\\r\\n                 open CAN at 500 kbit/s, channel 5
  atf5 1 64 4\\r\\n + mask + pattern  -> arf5 0 0\\r\\n     PASS filter
  att5 <n> 64\\r\\n + n bytes   -> ar5 <echo frame> + aro\\r\\n   transmit (id BE + data)
  RX frames: 'ar5' len kind ts(4, BE us) payload(len - 5) = id(4) + data
  atc5\\r\\n -> aro\\r\\n close channel; atz\\r\\n reset

Prints the same lines as j2534_tp20_tester.py (write RTT, per block, the 2 MB
projection). --wait-ack makes every transmit wait for the device's 'aro' (what
a J2534 DLL does for a non-zero Timeout); default = queue and return.
64-bit Python, ctypes only (setupapi + winusb).
"""
import ctypes
import statistics
import struct
import sys
import threading
import time

GUID_STR = "{C2E6EBE6-2FC5-4781-A4E5-B993B37CB069}"
BLOCKS = int(sys.argv[sys.argv.index("--blocks") + 1]) if "--blocks" in sys.argv else 40
WAIT_ACK = "--wait-ack" in sys.argv
REQ_ID, DATA_ID = 0x740, 0x300
FRAMES_PER_BLOCK = 37

k32 = ctypes.windll.kernel32
setupapi = ctypes.windll.setupapi
winusb = ctypes.windll.winusb
ole32 = ctypes.windll.ole32


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("InterfaceClassGuid", GUID), ("Flags", ctypes.c_ulong),
                ("Reserved", ctypes.c_void_p)]


class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ubyte) for n in ("bLength", "bDescriptorType", "bInterfaceNumber", "bAlternateSetting",
                                                "bNumEndpoints", "bInterfaceClass", "bInterfaceSubClass",
                                                "bInterfaceProtocol", "iInterface")]


class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [("PipeType", ctypes.c_int), ("PipeId", ctypes.c_ubyte), ("MaximumPacketSize", ctypes.c_ushort),
                ("Interval", ctypes.c_ubyte)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_ulong,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                                      ctypes.c_void_p]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                            ctypes.c_ulong, ctypes.c_void_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]
winusb.WinUsb_Initialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
winusb.WinUsb_Free.argtypes = [ctypes.c_void_p]
winusb.WinUsb_GetAssociatedInterface.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_void_p)]
winusb.WinUsb_QueryInterfaceSettings.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(USB_INTERFACE_DESCRIPTOR)]
winusb.WinUsb_QueryPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(WINUSB_PIPE_INFORMATION)]
winusb.WinUsb_SetPipePolicy.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
winusb.WinUsb_WritePipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
winusb.WinUsb_ReadPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_ulong,
                                   ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]


def device_path():
    guid = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(GUID_STR), ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)   # PRESENT | DEVICEINTERFACE
    if hdev is None or hdev == ctypes.c_void_p(-1).value:
        raise RuntimeError("SetupDiGetClassDevs failed")
    ifd = SP_DEVICE_INTERFACE_DATA()
    ifd.cbSize = ctypes.sizeof(ifd)
    if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), 0, ctypes.byref(ifd)):
        raise RuntimeError("no Openport 2.0 WinUSB interface present (error %d)" % k32.GetLastError())
    need = ctypes.c_ulong(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0, ctypes.byref(need), None)
    buf = ctypes.create_string_buffer(need.value)
    ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0] = 8   # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W on x64
    if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), buf, need, None, None):
        raise RuntimeError("SetupDiGetDeviceInterfaceDetail failed (error %d)" % k32.GetLastError())
    return ctypes.wstring_at(ctypes.addressof(buf) + 4)


class Openport:
    def __init__(self):
        path = device_path()
        self.h = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0x80 | 0x40000000, None)
        if self.h is None or self.h == ctypes.c_void_p(-1).value:
            raise RuntimeError("CreateFile failed (error %d): is another driver holding the Openport?" % k32.GetLastError())
        self.ih = ctypes.c_void_p()
        if not winusb.WinUsb_Initialize(self.h, ctypes.byref(self.ih)):
            raise RuntimeError("WinUsb_Initialize failed (error %d)" % k32.GetLastError())
        self.pipe_in = self.pipe_out = None
        self.find_pipes(self.ih)
        if self.pipe_in is None:
            for i in range(3):
                ih2 = ctypes.c_void_p()
                if winusb.WinUsb_GetAssociatedInterface(self.ih, i, ctypes.byref(ih2)):
                    self.find_pipes(ih2)
                    if self.pipe_in is not None:
                        self.ih = ih2
                        break
        if self.pipe_in is None or self.pipe_out is None:
            raise RuntimeError("no bulk IN/OUT pipe pair")
        to = ctypes.c_ulong(20)
        winusb.WinUsb_SetPipePolicy(self.ih, self.pipe_in, 3, 4, ctypes.byref(to))   # PIPE_TRANSFER_TIMEOUT 20 ms
        self.buf = bytearray()
        self.frames = []              # (t_rx, kind, ts_us, payload bytes)
        self.lines = []               # (t_rx, text) for ari/aro/arf/are
        self.cv = threading.Condition()
        self.run = True
        self.desync = 0
        self.thread = threading.Thread(target=self.reader, daemon=True)
        self.thread.start()

    def find_pipes(self, ih):
        desc = USB_INTERFACE_DESCRIPTOR()
        if not winusb.WinUsb_QueryInterfaceSettings(ih, 0, ctypes.byref(desc)):
            return
        for i in range(desc.bNumEndpoints):
            pi = WINUSB_PIPE_INFORMATION()
            if winusb.WinUsb_QueryPipe(ih, 0, i, ctypes.byref(pi)) and pi.PipeType == 2:
                if pi.PipeId & 0x80:
                    self.pipe_in = pi.PipeId
                else:
                    self.pipe_out = pi.PipeId

    def write(self, data):
        n = ctypes.c_ulong(0)
        b = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        if not winusb.WinUsb_WritePipe(self.ih, self.pipe_out, b, len(data), ctypes.byref(n), None):
            raise RuntimeError("WinUsb_WritePipe failed (error %d)" % k32.GetLastError())

    def reader(self):
        cap = 4096
        b = (ctypes.c_ubyte * cap)()
        got = ctypes.c_ulong(0)
        while self.run:
            ok = winusb.WinUsb_ReadPipe(self.ih, self.pipe_in, b, cap, ctypes.byref(got), None)
            t = time.perf_counter()
            if not ok:
                err = k32.GetLastError()
                if err == 121:         # ERROR_SEM_TIMEOUT: nothing in 20 ms
                    continue
                with self.cv:
                    self.lines.append((t, "READ ERROR %d" % err))
                    self.cv.notify_all()
                break
            if got.value:
                self.buf += bytes(b[:got.value])
                self.parse(t)

    def parse(self, t):
        out_frames, out_lines = [], []
        while len(self.buf) >= 3:
            if self.buf[0:2] != b"ar":
                self.desync += 1
                nl = self.buf.find(b"\r\n")
                self.buf = self.buf[nl + 2:] if nl >= 0 else bytearray()
                continue
            c = self.buf[2:3]
            if c in (b"i", b"o", b"f", b"e"):
                nl = self.buf.find(b"\r\n")
                if nl < 0:
                    break
                out_lines.append((t, self.buf[:nl].decode(errors="replace")))
                self.buf = self.buf[nl + 2:]
                continue
            if len(self.buf) < 4:
                break
            ln = self.buf[3]
            if len(self.buf) < 4 + ln:
                break
            body = bytes(self.buf[4:4 + ln])
            self.buf = self.buf[4 + ln:]
            kind = body[0]
            ts_us = struct.unpack(">I", body[1:5])[0]
            out_frames.append((t, kind, ts_us, body[5:]))
        if out_frames or out_lines:
            with self.cv:
                self.frames.extend(out_frames)
                self.lines.extend(out_lines)
                self.cv.notify_all()

    def wait_line(self, prefix, timeout):
        end = time.perf_counter() + timeout
        with self.cv:
            while True:
                for i, (t, s) in enumerate(self.lines):
                    if s.startswith(prefix):
                        del self.lines[:i + 1]
                        return s
                left = end - time.perf_counter()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def wait_frame(self, timeout):
        end = time.perf_counter() + timeout
        with self.cv:
            while True:
                if self.frames:
                    return self.frames.pop(0)
                left = end - time.perf_counter()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def cmd(self, text, reply, payload=b"", timeout=2.0):
        self.write(text.encode() + b"\r\n" + payload)
        r = self.wait_line(reply, timeout)
        if r is None:
            raise RuntimeError("no %r reply to %r" % (reply, text))
        return r

    def transmit(self, can_id, frame, wait_ack):
        data = struct.pack(">I", can_id) + bytes(frame)
        t0 = time.perf_counter()
        self.write(("att5 %d 64\r\n" % len(data)).encode() + data)
        t_call = time.perf_counter() - t0
        t_ack = None
        if wait_ack:
            if self.wait_line("aro", 2.0) is None:
                raise RuntimeError("no aro after att")
            t_ack = time.perf_counter() - t0
        return t_call, t_ack

    def close(self):
        self.run = False
        self.thread.join(1.0)
        winusb.WinUsb_Free(self.ih)
        k32.CloseHandle(self.h)


def main():
    op = Openport()
    ident = op.cmd("ati", "ari")
    op.cmd("ata", "aro")
    op.cmd("ato5 0 500000 0", "aro")
    filt = op.cmd("atf5 1 64 4", "arf", struct.pack(">II", 0, 0))
    print("openport: %s; channel 5 CAN 500000 open; filter reply %r; pipes in 0x%02X out 0x%02X; mode %s" % (
        ident, filt, op.pipe_in, op.pipe_out, "wait for the device's aro per transmit" if WAIT_ACK else "queue and return"), flush=True)
    with op.cv:
        op.frames.clear()
    wr_call, wr_ack, ack_call, ack_ack, blk_times, fails = [], [], [], [], [], []
    total_bytes = 0
    seq = 0
    t_all = time.perf_counter()
    for blk in range(BLOCKS):
        t0 = time.perf_counter()
        tc, ta = op.transmit(REQ_ID, [0x10 | seq, 0x00, 0x02, 0x1A, blk & 0xFF], WAIT_ACK)
        seq = (seq + 1) & 0xF
        wr_call.append(tc)
        if ta is not None:
            wr_ack.append(ta)
        got = 0
        expect_seq = None
        deadline = time.perf_counter() + 5.0
        done = False
        while not done and time.perf_counter() < deadline:
            fr = op.wait_frame(0.05)
            if fr is None:
                continue
            _, kind, ts_us, payload = fr
            if len(payload) < 5 or struct.unpack(">I", payload[:4])[0] != DATA_ID:
                continue
            op_, s = payload[4] >> 4, payload[4] & 0xF
            if expect_seq is not None and s != expect_seq:
                fails.append("block %d: frame %d sequence %X, expected %X" % (blk, got, s, expect_seq))
            expect_seq = (s + 1) & 0xF
            got += 1
            total_bytes += len(payload) - 5
            if op_ in (0x0, 0x1):
                tc, ta = op.transmit(REQ_ID, [0xB0 | expect_seq], WAIT_ACK)
                ack_call.append(tc)
                if ta is not None:
                    ack_ack.append(ta)
            if op_ == 0x1:
                done = True
        if not done:
            fails.append("block %d: incomplete, %d of %d frames within 5 s" % (blk, got, FRAMES_PER_BLOCK))
            break
        blk_times.append(time.perf_counter() - t0)
    elapsed = time.perf_counter() - t_all
    op.transmit(REQ_ID, [0xA8], True)
    op.cmd("atc5", "aro")
    op.write(b"atz\r\n")
    time.sleep(0.2)
    op.close()

    def line(name, xs):
        if xs:
            xs = sorted(xs)
            print("%s (n=%d): median %.2f ms, p95 %.2f ms, max %.2f ms" % (
                name, len(xs), statistics.median(xs) * 1000, xs[int(len(xs) * 0.95)] * 1000, xs[-1] * 1000), flush=True)
    line("request write: the USB write call", wr_call)
    line("request write: until the device's aro (frame on the bus)", wr_ack)
    line("ACK write: the USB write call", ack_call)
    line("ACK write: until the device's aro (frame on the bus)", ack_ack)
    if blk_times:
        bps = (len(blk_times) * 255) / sum(blk_times)
        print("blocks %d, %d data bytes in %.2f s: %.0f B/s, per block median %.1f ms, max %.1f ms; PROJECTED 2 MB read: %.1f min" % (
            len(blk_times), len(blk_times) * 255, sum(blk_times), bps, statistics.median(blk_times) * 1000,
            max(blk_times) * 1000, 2097152 / bps / 60.0), flush=True)
    if op.desync:
        print("parser desyncs: %d" % op.desync, flush=True)
    if fails:
        print("TESTER FAIL: " + "; ".join(fails[:6]), flush=True)
        sys.exit(1)
    print("TESTER DONE", flush=True)


if __name__ == "__main__":
    main()
