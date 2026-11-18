#!/usr/bin/env python3
"""The admin password on the web server and on a WebSocket -> HTTP AUTH PASS.

With `http_server_manager.auth_enabled` on, every request and every
WebSocket handshake must carry the password. This bench switches it on with
a password of its own, checks both sides, and switches it off again:

  A1  a request without the password is answered 401 with the Basic prompt
  A2  the same request with `Authorization: Basic` (any user name) is 200
  A3  with the `wican_auth` cookie it is 200
  A4  a wrong password is 401
  W1  a WebSocket handshake without the password is refused
  W2  a WebSocket session opened WITH the password stays a WebSocket: the
      frames the client sends afterwards are answered with frames, and no
      HTTP text ever arrives in the stream
  R   the settings back as found, only the planned restarts, no new E line

W2 is why the bench exists (2026-11-18): the password check ran again for
every data frame of an open session, found no HTTP headers on a frame, and
wrote `HTTP/1.1 401 Unauthorized` into the WebSocket stream.

If the bench dies with the password still on, the unit asks for it: the
password is the constant BENCH_PW below (any user name), and
`--restore-only` switches it off.

  python http_auth_bench.py --dut localhost:18081 [--restore-only]
Expected final line: HTTP AUTH PASS
"""
import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

BENCH_PW = "bench-auth-2026"
WS_PATH = "/ws/obd"

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def basic(pw, user="bench"):
    return "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()


class Dut:
    def __init__(self, base):
        self.host, self.port = base.split(":")[0], int(base.split(":")[1]) if ":" in base else 80
        self.base = "http://" + base

    def req(self, path, method="GET", body=None, headers=None, timeout=10):
        """(status, headers, text); the status of a refusal too"""
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode(errors="replace")

    def api(self, path, method="GET", body=None, pw=None):
        st, _, text = self.req(path, method, body, {"Authorization": basic(pw)} if pw else None)
        if st != 200:
            raise RuntimeError("%s %s -> HTTP %d %s" % (method, path, st, text[:80]))
        return json.loads(text) if text.strip().startswith(("{", "[")) else text

    def wait_up(self, pw=None, secs=150):
        end = time.time() + secs
        while time.time() < end:
            try:
                return self.api("/api/status", pw=pw)
            except Exception:
                time.sleep(2)
        return None

    def settings(self, pw=None):
        s = self.api("/api/settings/http_server_manager", pw=pw)
        s.pop("degraded", None)
        s.pop("pending_reboot", None)
        return s

    def submit(self, obj, pw, why):
        self.api("/api/settings/http_server_manager", "PUT", obj, pw=pw)
        r = self.api("/api/settings/submit", "POST", pw=pw)
        if r.get("reboot"):
            print("restart (%s)..." % why, flush=True)
            time.sleep(6)
            return True
        return False


def ws_open(dut, pw=None):
    """(socket or None, the first line of the unit's answer)"""
    s = socket.create_connection((dut.host, dut.port), timeout=8)
    key = base64.b64encode(os.urandom(16)).decode()
    head = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n" % (WS_PATH, dut.host, key))
    if pw:
        head += "Authorization: %s\r\n" % basic(pw)
    s.sendall((head + "\r\n").encode())
    got = b""
    try:
        while b"\r\n\r\n" not in got and len(got) < 4096:
            d = s.recv(1)
            if not d:
                break
            got += d
    except OSError:
        pass
    first = got.split(b"\r\n")[0].decode(errors="replace")
    if b" 101 " not in got.split(b"\r\n")[0]:
        s.close()
        return None, first
    return s, first


def ws_text(payload):
    mask = os.urandom(4)
    return struct.pack("!BB", 0x81, 0x80 | len(payload)) + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))


def ws_read(s, secs):
    """everything the unit sends in `secs`: (raw bytes, payloads of the frames
    that parse, True when all of it parsed as frames)"""
    raw = b""
    s.settimeout(0.3)
    end = time.time() + secs
    while time.time() < end:
        try:
            d = s.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not d:
            break
        raw += d
    payloads, rest = [], raw
    while len(rest) >= 2:
        op, n, off = rest[0] & 0x0F, rest[1] & 0x7F, 2
        if rest[0] & 0x70 or rest[1] & 0x80 or op not in (0, 1, 2, 8, 9, 10):
            return raw, payloads, False          # not a frame a server sends
        if n == 126:
            if len(rest) < 4:
                break
            n, off = struct.unpack("!H", rest[2:4])[0], 4
        elif n == 127:
            return raw, payloads, False
        if len(rest) < off + n:
            break
        payloads.append(rest[off:off + n])
        rest = rest[off + n:]
    return raw, payloads, len(rest) == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--restore-only", action="store_true")
    a = ap.parse_args()
    dut = Dut(a.dut)
    t0 = time.time()

    st, _, _ = dut.req("/api/status")
    locked = st == 401
    if a.restore_only or locked:
        if not locked:
            print("HTTP AUTH: the unit asks for no password, nothing to restore")
            return
        print("the unit asks for a password: switching the bench's password off", flush=True)
        s = dut.settings(BENCH_PW)
        s.update({"auth_enabled": False, "auth_password": ""})
        dut.submit(s, BENCH_PW, "the password off")
        ok = dut.wait_up() is not None
        print("HTTP AUTH RESTORE " + ("DONE" if ok else "FAILED"))
        sys.exit(0 if ok and a.restore_only else 1)

    status = dut.api("/api/status")
    boot0, unexp0 = status.get("boot_count"), status.get("unexpected_resets")
    err0 = (status.get("health") or {}).get("log_errors")
    found = dut.settings()
    print("dut: %s boot_count=%s; as found: auth_enabled=%s" % (status.get("version"), boot0, found.get("auth_enabled")),
          flush=True)
    if found.get("auth_enabled"):
        print("HTTP AUTH FAIL: a password is already on and it is not this bench's")
        sys.exit(1)
    planned = 0

    try:
        on = dict(found)
        on.update({"auth_enabled": True, "auth_password": BENCH_PW})
        planned += 1 if dut.submit(on, None, "the password on") else 0
        if dut.wait_up(BENCH_PW) is None:
            raise RuntimeError("the unit did not come back with the password on")
        time.sleep(3)

        st, hdr, _ = dut.req("/api/status")
        check("A1 a request without the password: 401 with the Basic prompt",
              st == 401 and "Basic" in hdr.get("WWW-Authenticate", ""), (st, hdr.get("WWW-Authenticate")))
        st, _, _ = dut.req("/api/status", headers={"Authorization": basic(BENCH_PW, "anyone")})
        check("A2 with Authorization Basic (any user name): 200", st == 200, st)
        st, _, _ = dut.req("/api/status", headers={"Cookie": "wican_auth=" + BENCH_PW})
        check("A3 with the wican_auth cookie: 200", st == 200, st)
        st, _, _ = dut.req("/api/status", headers={"Authorization": basic(BENCH_PW + "x")})
        check("A4 a wrong password: 401", st == 401, st)

        s, first = ws_open(dut)
        check("W1 a WebSocket handshake without the password is refused", s is None and " 101 " not in first, first)
        if s is not None:
            s.close()

        s, first = ws_open(dut, BENCH_PW)
        if check("W2 a WebSocket handshake with the password is accepted", s is not None, first):
            ws_read(s, 1.0)                      # whatever the chip had to say first
            s.sendall(ws_text(b"\r"))            # wake the chip's prompt
            time.sleep(0.5)
            s.sendall(ws_text(b"ATI\r"))
            raw, payloads, clean = ws_read(s, 4.0)
            text = b"".join(payloads)
            check("W2 the frames sent on the open session are answered with frames, no HTTP text in the stream",
                  clean and b"HTTP/1." not in raw and len(text) > 0,
                  {"bytes": len(raw), "frames": len(payloads), "http_in_stream": b"HTTP/1." in raw,
                   "start": raw[:60].decode(errors="replace")})
            s.close()
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        print("== restore", flush=True)
        try:
            off = dict(found)
            off.update({"auth_enabled": False, "auth_password": ""})
            planned += 1 if dut.submit(off, BENCH_PW, "the password off") else 0
            status = dut.wait_up() or {}
            back = dut.settings() if status else {}
            check("R1 the password is off again, the settings as found", back == found,
                  {k: back.get(k) for k in ("auth_enabled",)})
            check("R2 only the planned restarts (%d)" % planned,
                  status.get("boot_count") == boot0 + planned and status.get("unexpected_resets") == unexp0,
                  {"boot_count": (boot0, status.get("boot_count")), "unexpected": (unexp0, status.get("unexpected_resets"))})
            err1 = (status.get("health") or {}).get("log_errors")
            check("R3 no new E lines", err1 == err0, (err0, err1))
        except Exception as e:
            check("the restore ran to the end", False, "%s: %s (run again with --restore-only; the password is %s)"
                  % (type(e).__name__, e, BENCH_PW))

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("HTTP AUTH FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("HTTP AUTH PASS")


if __name__ == "__main__":
    main()
