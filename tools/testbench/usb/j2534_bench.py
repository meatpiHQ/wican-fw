#!/usr/bin/env python3
"""J2534 PassThru server Phase-1 bench (run ON rpi001, or anywhere with
IP reach to the DUT). Drives the wire protocol over TCP — the same
protocol the USB-CDC transport will carry in Phase 3.

Exercises the session/channel handshake: HELLO → OPEN → CONNECT(CAN),
CONNECT(ISO15765), CONNECT(unsupported→ERR), DISCONNECT, CLOSE, and
confirms a Phase-2 op (WRITE_MSGS) answers ERR_NOT_SUPPORTED cleanly.

Usage: j2534_bench.py <dut_ip> [port]     Expected: J2534 TARGET PASS
"""
import socket
import struct
import sys

_pos = [a for a in sys.argv[1:] if not a.startswith("--")]
DUT = _pos[0] if len(_pos) > 0 else "10.42.0.62"
PORT = int(_pos[1]) if len(_pos) > 1 else 6809

MAGIC = 0x4A35
HDR = struct.Struct("<HBBHHI")  # magic, ver, type, seq, channel, length
(HELLO, OPEN, CLOSE, CONNECT, DISCONNECT, WRITE_MSGS, ACK, RX_MSG) = (
    0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x80, 0x81)
PROT_CAN, PROT_ISO15765, PROT_J1850VPW = 5, 6, 1
NOERROR, ERR_NOT_SUPPORTED, ERR_DEVICE_NOT_CONNECTED = 0x00, 0x01, 0x08
ERR_INVALID_MSG = 0x0A
MSG = struct.Struct("<IIIIII")  # protocol, rx_status, tx_flags, ts, extra, size

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


class Client:
    def __init__(self, ip, port):
        self.s = socket.create_connection((ip, port), timeout=5)
        self.seq = 0

    def _recv(self, n):
        b = b""
        while len(b) < n:
            chunk = self.s.recv(n - len(b))
            if not chunk:
                raise EOFError
            b += chunk
        return b

    def _read_frame(self):
        magic, ver, rt, rseq, rch, rlen = HDR.unpack(self._recv(HDR.size))
        assert magic == MAGIC, "bad magic"
        return rt, rseq, rch, self._recv(rlen)

    def call(self, mtype, channel=0, payload=b""):
        self.seq = (self.seq + 1) & 0xFFFF
        self.s.sendall(HDR.pack(MAGIC, 1, mtype, self.seq, channel,
                                len(payload)) + payload)
        # skip any async RX_MSG that arrives before our ACK
        while True:
            rt, rseq, rch, body = self._read_frame()
            if rt == ACK:
                break
        status = struct.unpack_from("<I", body, 0)[0]
        result = (struct.unpack_from("<I", body, 4)[0]
                  if len(body) >= 8 else None)
        return status, result, rch

    def write_uds(self, channel, uds_bytes, collect_ms=1200):
        """WRITE_MSGS one ISO15765 message, then collect RX_MSG payloads."""
        msg = MSG.pack(PROT_ISO15765, 0, 0, 0, 0, len(uds_bytes)) + uds_bytes
        payload = struct.pack("<I", 1) + msg
        st, _, _ = self.call(WRITE_MSGS, channel, payload)
        rxs = []
        self.s.settimeout(collect_ms / 1000.0)
        try:
            while True:
                rt, rseq, rch, body = self._read_frame()
                if rt == RX_MSG:
                    size = MSG.unpack_from(body, 0)[5]
                    rxs.append(body[MSG.size:MSG.size + size])
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.s.settimeout(5)
        return st, rxs


def main():
    try:
        c = Client(DUT, PORT)
    except OSError as e:
        print(f"J2534 TARGET FAIL: cannot connect {DUT}:{PORT} ({e})")
        sys.exit(1)

    st, ver, _ = c.call(HELLO)
    check("HELLO", st == NOERROR and ver == 1, f"wire v{ver}")

    # CONNECT before OPEN must be rejected
    st, _, _ = c.call(CONNECT, payload=struct.pack("<III", PROT_CAN, 0, 500000))
    check("CONNECT before OPEN rejected", st == ERR_DEVICE_NOT_CONNECTED)

    st, devid, _ = c.call(OPEN)
    check("OPEN", st == NOERROR, f"device id {devid}")

    st, ch1, hdr_ch = c.call(CONNECT,
                             payload=struct.pack("<III", PROT_CAN, 0, 500000))
    check("CONNECT CAN", st == NOERROR and ch1 == 1, f"channel {ch1}")

    st, ch2, _ = c.call(CONNECT,
                        payload=struct.pack("<III", PROT_ISO15765, 0x40, 500000))
    check("CONNECT ISO15765", st == NOERROR and ch2 == 2, f"channel {ch2}")

    st, _, _ = c.call(CONNECT,
                     payload=struct.pack("<III", PROT_J1850VPW, 0, 0))
    check("CONNECT unsupported → ERR_NOT_SUPPORTED", st == ERR_NOT_SUPPORTED)

    # WRITE_MSGS is wired (Phase 2): an empty write is handled, not a hang
    st, _, _ = c.call(WRITE_MSGS, channel=ch1, payload=struct.pack("<I", 0))
    check("WRITE_MSGS handled (empty -> ERR_INVALID_MSG)",
          st == ERR_INVALID_MSG)

    st, _, _ = c.call(DISCONNECT, channel=ch1)
    check("DISCONNECT ch1", st == NOERROR)

    st, _, _ = c.call(CLOSE)
    check("CLOSE", st == NOERROR)

    if fails:
        print("J2534 TARGET FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 TARGET PASS")


def reflash():
    """End-to-end ISO15765 reflash preamble through the J2534 server to a
    PCAN reflash ECU (start pcan_reflash_ecu.py --scenario happy first;
    pause autopid so its OBD poll doesn't collide on 0x7E8)."""
    try:
        c = Client(DUT, PORT)
    except OSError as e:
        print(f"J2534 REFLASH FAIL: cannot connect {DUT}:{PORT} ({e})")
        sys.exit(1)

    c.call(HELLO)
    c.call(OPEN)
    # CONNECT ISO15765 with tx=7E0 rx=7E8 (extended CONNECT payload)
    payload = struct.pack("<IIIII", PROT_ISO15765, 0, 500000, 0x7E0, 0x7E8)
    st, ch, _ = c.call(CONNECT, payload=payload)
    check("CONNECT ISO15765 (tx 7E0 / rx 7E8)", st == NOERROR)

    def last(rxs):
        return rxs[-1].hex(" ").upper() if rxs else "(none)"

    st, rx = c.write_uds(ch, bytes([0x10, 0x02]))
    check("session 10 02 -> 50 02", rx and rx[-1][:2] == b"\x50\x02", last(rx))

    st, rx = c.write_uds(ch, bytes([0x27, 0x01]))
    seed = rx[-1][2:] if rx and rx[-1][:2] == b"\x67\x01" else b""
    check("seed 27 01 -> 67 01 + seed", len(seed) == 4, last(rx))

    key = bytes(b ^ 0xFF for b in seed)  # ReflashEcu toy KDF: seed XOR 0xFF
    st, rx = c.write_uds(ch, bytes([0x27, 0x02]) + key)
    check("key 27 02 -> 67 02 (unlocked)",
          rx and rx[-1][:2] == b"\x67\x02", last(rx))

    st, rx = c.write_uds(ch, bytes([0x34, 0x00, 0x44, 0, 0, 0x04, 0x00]))
    check("requestDownload 34 -> 74", rx and rx[-1][0] == 0x74, last(rx))

    # erase routine: ECU floods 0x78 (if erase_pending) then 71 01 FF 00 00
    st, rx = c.write_uds(ch, bytes([0x31, 0x01, 0xFF, 0x00]), collect_ms=2000)
    pend = sum(1 for m in rx if m[:1] == b"\x7F" and len(m) >= 3
               and m[2] == 0x78)
    final = rx[-1] if rx else b""
    check("erase 31 01 FF00 -> 71 (rode out %d×0x78)" % pend,
          final[:1] == b"\x71", last(rx))

    c.call(DISCONNECT, channel=ch)
    c.call(CLOSE)

    if fails:
        print("J2534 REFLASH FAIL:", ", ".join(fails))
        sys.exit(1)
    print("J2534 REFLASH PASS")


if __name__ == "__main__":
    if "--reflash" in sys.argv:
        reflash()
    else:
        main()
