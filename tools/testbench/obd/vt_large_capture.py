#!/usr/bin/env python3
"""Large-payload ELM legs (run ON the bench Pi, the app's seat): drives
the MIC chip through the WiCAN's obd0 TCP port against a UDS "memory ECU"
(byte at address A = A & 0xFF; 0x23 ReadMemoryByAddress serves it up to
4094 bytes, 0x3D WriteMemoryByAddress / 0x36 TransferData verify it).

The default framing is the MTPS-OBD tester's (wican_pro_tester/
MTPS-OBD+Log.TXT, HS-CAN block): headers ON, spaces OFF, `ATSH7E0`, the
write sent as `VTFullyRequestCk0FFF36<bsc><data><CCCC> 1` and answered
`7E87F3678` (response pending) then `7E87600`; a long read comes back as
ONE line `7E863<hex...>` (that is the "4 KB data = 8 KB ASCII" case).

  tx  VTFullyRequestCk carrying `36 <bsc> <N pattern bytes>` (or a 3D
      write with --svc 3D): the ~8 KB ASCII line must cross TCP -> bridge
      -> UART intact and the chip must transmit the ISO-TP transfer; the
      positive 76/7D proves every byte arrived at the ECU.
  rx  `23 23 <addr> <size> 1` for size up to 4094: the chip receives the
      multi-frame response and prints it; every byte is checked.

Prints one `RESULT {json}` line per leg and `DONE` at the end.

usage: vt_large_capture.py HOST [--port 35000] [--tx 64,512,2048,4064]
          [--rx 64,512,2048,4094] [--txid 7E0] [--rxid 7E8]
          [--svc 36|3D] [--no-headers] [--spaces] [--hint] [--no-al]
          [--raw-dir DIR]
"""
import argparse
import json
import re
import socket
import sys
import time

PREFIX_RE = re.compile(r"^\s*[0-9A-F]{1,3}:\s*")   # "0: " ISO-TP line numbers


class Link:
    """TCP socket (the WiCAN) or a serial port (a USB ELM adapter such as
    a vLinker FS / OBDLink) behind one send/recv/close face."""

    def __init__(self, host=None, port=35000, serial_port=None,
                 baud=115200):
        self.kind = "serial" if serial_port else "tcp"
        if serial_port:
            try:
                import serial
            except ImportError:
                sys.exit("pyserial is required for --serial")
            try:
                self.ser = serial.Serial(serial_port, baud, timeout=0.5)
            except serial.SerialException as e:
                sys.exit(f"cannot open {serial_port}: {e}")
        else:
            self.sock = socket.create_connection((host, port), timeout=5)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, data):
        if self.kind == "tcp":
            self.sock.sendall(data)
        else:
            self.ser.write(data)
            self.ser.flush()

    def recv(self, timeout):
        if self.kind == "tcp":
            self.sock.settimeout(timeout)
            try:
                d = self.sock.recv(65536)
            except socket.timeout:
                return b""
            if not d:
                raise ConnectionError("closed")
            return d
        self.ser.timeout = timeout
        d = self.ser.read(1)
        if d:
            d += self.ser.read(self.ser.in_waiting)
        return d

    def close(self):
        if self.kind == "tcp":
            self.sock.close()
        else:
            self.ser.close()


def xact(link, cmd, timeout=3.0):
    link.send(cmd.encode() + b"\r")
    t0 = time.perf_counter()
    buf = b""
    first = None
    while True:
        remaining = timeout - (time.perf_counter() - t0)
        if remaining <= 0:
            break
        d = link.recv(remaining)
        if not d:
            break
        if first is None:
            first = time.perf_counter()
        buf += d
        if buf.rstrip().endswith(b">"):
            break
    t1 = time.perf_counter()
    return buf.decode(errors="replace"), (t1 - t0) * 1000, \
        ((first - t0) * 1000 if first else None)


def lines_of(text, cmd_prefixes=("VT", "23")):
    out = []
    for ln in text.replace("\n", "\r").split("\r"):
        ln = ln.strip()
        if not ln or ln == ">" or ln.startswith(cmd_prefixes):
            continue
        out.append(ln)
    return out


def line_bytes(ln, headers, rxid):
    """One chip line -> (payload bytes, declared total length or None), or
    None when the line is not data. The MIC prints two shapes:
      headers OFF (CAF1 formatting): a length line (`041`, `FFF`) then
        `0: 63 00 01 02 03 04`, `1: 05 06 ..` rows (row index = one hex
        digit, wraps) - plain data, no PCI;
      headers ON: one RAW frame per line, `7EC 10 41 63 00 01 02 03 04`,
        `7EC 21 05 06 ..`, `7EC 03 7F 36 78` - the ISO-TP PCI leads: SF ->
        its data, FF -> length + the bytes after the 2-byte PCI, CF -> the
        bytes after the PCI, FC -> nothing.
    The PCI rule is applied ONLY to header-prefixed raw lines: a formatted
    row whose first data byte happens to be 0x1x/0x2x would otherwise be
    mistaken for a First/Consecutive Frame (bench 2026-09-08, `3: 14 15
    ..`)."""
    if ln.startswith("FC:"):
        return None   # the chip reports flow-control frames it saw
    m = PREFIX_RE.match(ln)
    is_row = m is not None
    ln = PREFIX_RE.sub("", ln)
    flat = ln.replace(" ", "")
    hdr = rxid.upper()
    is_raw = False
    if headers and flat.startswith(hdr) and not is_row:
        flat = flat[len(hdr):]
        is_raw = True
    if not re.fullmatch(r"[0-9A-F]+", flat) or len(flat) % 2:
        return None
    b = bytes.fromhex(flat)
    if not b:
        return None
    if not is_raw:
        return b, None                        # formatted row / plain line
    pci = b[0] >> 4
    if pci == 0 and (b[0] & 0xF) == len(b) - 1 and 1 <= len(b) - 1 <= 7:
        return b[1:], None                    # single frame
    if pci == 1 and len(b) >= 2:
        total = ((b[0] & 0xF) << 8) | b[1]
        return b[2:], total                   # first frame
    if pci == 2:
        return b[1:], None                    # consecutive frame
    if pci == 3:
        return None                           # flow control
    return b, None


def hex_bytes(text, headers, rxid):
    """All ECU payload bytes of a chip response, in order; a raw First
    Frame's declared length trims the last frame's padding."""
    out = bytearray()
    total = None
    for ln in lines_of(text):
        r = line_bytes(ln, headers, rxid)
        if not r:
            continue
        b, declared = r
        if declared is not None:
            total = declared
        out += b
    if total is not None and len(out) > total:
        del out[total:]
    return bytes(out)


def pattern(addr, n):
    return bytes((addr + i) & 0xFF for i in range(n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="WiCAN address (TCP)")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--serial", default="", help="COMx of a USB ELM adapter")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--tx", default="64,512,2048,4064")
    ap.add_argument("--rx", default="64,512,2048,4094")
    ap.add_argument("--raw-dir", default="")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--txid", default="7E0", help="ECU request id (ATSH)")
    ap.add_argument("--rxid", default="7E8", help="ECU response id (ATCRA)")
    ap.add_argument("--svc", default="36", choices=["36", "3D"],
                    help="write service: 36 TransferData (tester) or 3D")
    ap.add_argument("--no-headers", action="store_true", help="ATH0")
    ap.add_argument("--spaces", action="store_true", help="ATS1")
    ap.add_argument("--hint", action="store_true",
                    help="append the ' 1' expected-responses hint. With "
                         "headers ON the MIC then returns after the FIRST "
                         "FRAME of a multi-frame response (bench "
                         "2026-09-08: 64 B -> one FF line, 2 KB -> NO "
                         "DATA), so it is off by default")
    ap.add_argument("--no-al", action="store_true",
                    help="omit ATAL (the tester's VTSWGP string carries AL)")
    a = ap.parse_args()
    if not a.host and not a.serial:
        ap.error("give HOST or --serial COMx")
    headers = not a.no_headers
    hint = " 1" if a.hint else ""

    s = Link(host=a.host, port=a.port, serial_port=a.serial or None,
             baud=a.baud)
    time.sleep(0.3)
    xact(s, "", timeout=0.5)   # drain / sync the prompt
    init = {}
    cmds = ["ATZ", "ATE0", "ATL0", "ATS1" if a.spaces else "ATS0",
            "ATH1" if headers else "ATH0", "ATSP6",
            "ATSH" + a.txid.upper(), "ATCRA" + a.rxid.upper(),
            "ATAT0", "ATST64", "ATCAF1"] + ([] if a.no_al else ["ATAL"])
    txid = int(a.txid, 16)
    if not 0x7E0 <= txid <= 0x7E7:
        # outside the OBD physical range the chip does not send ISO-TP flow
        # control by itself (bench 2026-09-08: only the First Frame of a 23
        # read came back on 740/748); the ELM327 way is a user-defined FC
        # frame: header = our tx id, data 30 00 00, mode 1
        cmds += ["ATFCSH" + a.txid.upper(), "ATFCSD300000", "ATFCSM1"]
    for c in cmds:
        t, ms, _ = xact(s, c, timeout=6.0)
        init[c] = t.strip()[:40]
    print("INIT " + json.dumps(init))

    addr = 0x000100
    bsc = 0
    for n in [int(x) for x in a.tx.split(",") if x]:
        if a.svc == "36":
            # TransferData: the tester's block shape (36 <bsc> <data>); the
            # memory ECU verifies the data against pattern(0, n)
            bsc = (bsc + 1) & 0xFF
            payload = bytes([0x36, bsc]) + pattern(0, n)
            want_sid = 0x76
        else:
            payload = bytes([0x3D, 0x23]) + addr.to_bytes(3, "big") + \
                n.to_bytes(2, "big") + pattern(addr, n)
            want_sid = 0x7D
        cmd = ("VTFullyRequestCk" + format(len(payload), "04X") +
               payload.hex().upper() + format(sum(payload) & 0xFFFF, "04X")
               + hint)
        text, ms, first_ms = xact(s, cmd, timeout=a.timeout)
        lines = lines_of(text)
        resp = hex_bytes(text, headers, a.rxid)
        # the positive response may follow one or more 7F xx 78 pendings
        flat_lines = [(line_bytes(ln, headers, a.rxid) or (b"", None))[0]
                      for ln in lines]
        pending = sum(1 for b in flat_lines if b[:1] == b"\x7F" and b[2:3] == b"\x78")
        ok = any(b[:1] == bytes([want_sid]) for b in flat_lines)
        res = {"leg": "tx", "n": n, "svc": a.svc, "cmd_chars": len(cmd) + 1,
               "ms": round(ms, 1),
               "first_ms": round(first_ms, 1) if first_ms else None,
               "ok": ok, "pending": pending, "lines": lines[:4],
               "resp_bytes": len(resp)}
        print("RESULT " + json.dumps(res))
        if a.raw_dir:
            with open(f"{a.raw_dir}/tx_{n}.txt", "w") as f:
                f.write(text)

    for n in [int(x) for x in a.rx.split(",") if x]:
        cmd = "2323" + format(addr, "06X") + format(n, "04X") + hint
        text, ms, first_ms = xact(s, cmd, timeout=a.timeout)
        resp = hex_bytes(text, headers, a.rxid)
        want = b"\x63" + pattern(addr, n)
        # headers-off formatting: the chip prints the total length as a
        # 3-digit line (`041`, `FFF`) - odd length, skipped by the parser -
        # and pads the last row; trim to the expected length when the
        # prefix matches
        if not headers and len(resp) > len(want) and \
                resp[:len(want)] == want:
            resp = resp[:len(want)]
        first_bad = next((i for i in range(min(len(resp), len(want)))
                          if resp[i] != want[i]), None)
        lines = lines_of(text)
        res = {"leg": "rx", "n": n, "ms": round(ms, 1),
               "first_ms": round(first_ms, 1) if first_ms else None,
               "chars": len(text), "got": len(resp) - 1, "want": n,
               "exact": resp == want, "first_bad": first_bad,
               "lines": len(lines),
               "line_max_chars": max((len(x) for x in lines), default=0),
               "head": text.strip()[:40], "tail": text.strip()[-40:]}
        print("RESULT " + json.dumps(res))
        if a.raw_dir:
            with open(f"{a.raw_dir}/rx_{n}.txt", "w") as f:
                f.write(text)

    xact(s, "ATCRA", timeout=2.0)
    xact(s, "ATSH7DF", timeout=2.0)
    s.close()
    print("DONE")


if __name__ == "__main__":
    main()
