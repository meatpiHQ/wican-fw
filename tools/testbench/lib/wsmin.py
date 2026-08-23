#!/usr/bin/env python3
"""Minimal dependency-free RFC6455 WebSocket client for the testbench.

Why not websocket-client: the bench environments (IDF venv on Windows,
rpi001) shouldn't need extra pip deps for a ~60-line protocol. Supports
what the bench needs: client handshake, masked binary/text send, frame
receive (blocking or drain-what's-there), ping->pong.

    ws = WS("192.168.82.1", 80, "/ws/can")
    ws.send(b"t1004DEADBEEF\r")            # binary frame (opcode 2)
    op, payload = ws.recv_frame()          # blocking, honors socket timeout
    for op, payload in ws.pump():          # non-blocking drain
        ...
"""
import base64
import os
import socket
import struct

OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 1, 2, 8, 9, 10


class WS:
    def __init__(self, host, port=80, path="/ws/can", timeout=5):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET {} HTTP/1.1\r\nHost: {}:{}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n"
               "\r\n").format(path, host, port, key)
        self.s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(1024)
            if not chunk:
                raise ConnectionError("WS handshake: connection closed")
            resp += chunk
        status = resp.split(b"\r\n", 1)[0]
        if b" 101" not in status:
            raise ConnectionError("WS handshake refused: " + status.decode())
        self.buf = resp.split(b"\r\n\r\n", 1)[1]  # bytes past the header

    def send(self, payload, opcode=OP_BIN):
        """Send one frame. Client frames must be masked (RFC6455 5.3)."""
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.s.sendall(hdr + mask + masked)

    def _parse_one(self):
        """One complete frame from self.buf, or None if incomplete."""
        if len(self.buf) < 2:
            return None
        b1 = self.buf[1]
        ln = b1 & 0x7F
        off = 2
        if ln == 126:
            if len(self.buf) < 4:
                return None
            ln = struct.unpack("!H", self.buf[2:4])[0]
            off = 4
        elif ln == 127:
            if len(self.buf) < 10:
                return None
            ln = struct.unpack("!Q", self.buf[2:10])[0]
            off = 10
        if b1 & 0x80:
            off += 4  # server frames shouldn't be masked, tolerate anyway
        if len(self.buf) < off + ln:
            return None
        op = self.buf[0] & 0x0F
        if b1 & 0x80:
            mask = self.buf[off - 4:off]
            payload = bytes(b ^ mask[i & 3]
                            for i, b in enumerate(self.buf[off:off + ln]))
        else:
            payload = self.buf[off:off + ln]
        self.buf = self.buf[off + ln:]
        return op, payload

    def recv_frame(self):
        """Blocking single frame (honors the socket timeout); pongs pings."""
        while True:
            f = self._parse_one()
            if f is not None:
                if f[0] == OP_PING:
                    self.send(f[1], OP_PONG)
                    continue
                return f
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("WS closed")
            self.buf += chunk

    def pump(self):
        """Non-blocking: drain the socket, return all complete frames."""
        try:
            while True:
                chunk = self.s.recv(65536)
                if not chunk:
                    break
                self.buf += chunk
        except (BlockingIOError, socket.timeout, OSError):
            pass
        frames = []
        while True:
            f = self._parse_one()
            if f is None:
                break
            if f[0] == OP_PING:
                try:
                    self.send(f[1], OP_PONG)
                except OSError:
                    pass
                continue
            frames.append(f)
        return frames

    def setblocking(self, flag):
        self.s.setblocking(flag)

    def settimeout(self, t):
        self.s.settimeout(t)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass
