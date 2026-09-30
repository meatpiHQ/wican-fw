#!/usr/bin/env python3
"""ELM327 request-loop latency probe: plays a Car Scanner style request
loop against an ELM327-class adapter and reports the per-request round
trip (command sent -> '>' prompt received), plus how many reads each
response needed and the gaps between them (Nagle / delayed-ACK / USB
latency-timer evidence).

Targets (one of):
  HOST [--port 35000]        the WiCAN's obd0 TCP server
  --serial COMx [--baud N]   a USB serial ELM adapter (OBDLink SX/EX,
                             ELM327 USB clones; needs pyserial). OBDLink
                             SX default 115200. FTDI-based adapters add
                             their "latency timer" (16 ms default on
                             Windows) to every response: set it to 1 ms
                             in Device Manager (port settings > advanced)
                             or the comparison is unfair to the adapter.

usage: elm_latency_probe.py HOST|--serial COMx [--port 35000] [--baud N]
                            [--n 200] [--cmd 010C] [--hint] [--label X]
                            [--raw FILE] [--init a,b,c]
Prints one `SUMMARY {json}` line and a `HIST` line; --raw stores every
sample. elm_compare.py imports run_loop() to build side-by-side tables.
"""
import argparse
import json
import socket
import statistics
import sys
import time

INIT = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP6", "ATAT1", "0100"]


class Link:
    """One byte stream to an ELM adapter: TCP socket or serial port."""

    def __init__(self, host=None, port=35000, serial_port=None,
                 baud=115200, ble=None, ble_write=None, ble_notify=None):
        self.kind = "serial" if serial_port else "ble" if ble else "tcp"
        if ble:
            self._ble_open(ble, ble_write, ble_notify)
        elif serial_port:
            try:
                import serial  # pyserial
            except ImportError:
                sys.exit("pyserial is required for --serial "
                         "(pip install pyserial)")
            try:
                self.ser = serial.Serial(serial_port, baud, timeout=0.3)
            except serial.SerialException as e:
                sys.exit(f"cannot open {serial_port}: {e} (list ports with "
                         "python -m serial.tools.list_ports -v)")
            self.name = f"{serial_port}@{baud}"
        else:
            self.sock = socket.create_connection((host, port), timeout=5)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.name = f"{host}:{port}"
        time.sleep(0.3)
        self.drain()

    # ---- BLE (a vLinker / OBDLink / clone with a GATT serial pipe) --------
    # UNTESTED against real hardware as of 2026-09-23: written for the
    # vLinker comparison leg, verify on the bench before quoting numbers.
    # Runs bleak on its own thread + event loop so the synchronous
    # request loop above stays identical for every transport.
    BLE_KNOWN = [  # (write uuid, notify uuid) pairs seen on ELM-class dongles
        # vLinker MC / OBDLink: service 18F0, 2AF1 write, 2AF0 notify (verified 2026-09-23)
        ("00002af1-0000-1000-8000-00805f9b34fb", "00002af0-0000-1000-8000-00805f9b34fb"),
        ("0000fff2-0000-1000-8000-00805f9b34fb", "0000fff1-0000-1000-8000-00805f9b34fb"),
        ("6e400002-b5a3-f393-e0a9-e50e24dcca9e", "6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
        ("0000ffe1-0000-1000-8000-00805f9b34fb", "0000ffe1-0000-1000-8000-00805f9b34fb"),
    ]

    def _ble_open(self, addr, write_uuid, notify_uuid):
        import asyncio
        import queue
        import threading
        try:
            from bleak import BleakClient
        except ImportError:
            sys.exit("bleak is required for --ble (pip install bleak)")
        self._q = queue.Queue()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._err = None

        def runner():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        threading.Thread(target=runner, daemon=True).start()

        async def connect():
            self._client = BleakClient(addr, timeout=20)
            await self._client.connect()
            chars = {}
            for svc in self._client.services:
                for ch in svc.characteristics:
                    chars[ch.uuid.lower()] = ch
            w, n = write_uuid, notify_uuid
            if not (w and n):
                for kw, kn in self.BLE_KNOWN:
                    if kw in chars and kn in chars:
                        w, n = kw, kn
                        break
            if not (w and n):
                ws = [u for u, c in chars.items()
                      if "write" in c.properties or "write-without-response" in c.properties]
                ns = [u for u, c in chars.items() if "notify" in c.properties]
                if ws and ns:
                    w, n = ws[0], ns[0]
            if not (w and n):
                raise RuntimeError(f"no write+notify pair found; chars: "
                                   f"{ {u: c.properties for u, c in chars.items()} }")
            self._w = chars[w]
            self._wwr = "write-without-response" in chars[w].properties
            await self._client.start_notify(chars[n], lambda _, d: self._q.put(bytes(d)))
            self.name = f"ble:{addr} w={w[:8]} n={n[:8]} mtu={self._client.mtu_size}"

        fut = asyncio.run_coroutine_threadsafe(connect(), self._loop)
        try:
            fut.result(timeout=40)
        except Exception as e:
            sys.exit(f"BLE connect failed: {e}")

    def _ble_write(self, data):
        import asyncio
        # ELM dongles take at most one MTU per write; 20 B is always safe
        for i in range(0, len(data), 20):
            fut = asyncio.run_coroutine_threadsafe(
                self._client.write_gatt_char(self._w, data[i:i + 20], response=not self._wwr),
                self._loop)
            fut.result(timeout=5)

    def drain(self):
        try:
            if self.kind == "tcp":
                self.sock.settimeout(0.3)
                self.sock.recv(4096)
            elif self.kind == "ble":
                import queue
                while True:
                    self._q.get(timeout=0.3)
            else:
                self.ser.timeout = 0.3
                self.ser.read(4096)
        except (socket.timeout, OSError):
            pass
        except Exception:  # queue.Empty
            pass

    def send(self, data):
        if self.kind == "tcp":
            self.sock.sendall(data)
        elif self.kind == "ble":
            self._ble_write(data)
        else:
            self.ser.write(data)
            self.ser.flush()

    def recv(self, timeout):
        """Up to one read's worth of bytes, or b'' on timeout."""
        if self.kind == "tcp":
            self.sock.settimeout(timeout)
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                return b""
            if not data:
                raise ConnectionError("closed")
            return data
        if self.kind == "ble":
            import queue
            try:
                return self._q.get(timeout=max(timeout, 0.001))
            except queue.Empty:
                return b""
        self.ser.timeout = timeout
        data = self.ser.read(1)
        if data:
            data += self.ser.read(self.ser.in_waiting)
        return data

    def close(self):
        if self.kind == "tcp":
            self.sock.close()
        elif self.kind == "ble":
            import asyncio
            try:
                asyncio.run_coroutine_threadsafe(self._client.disconnect(),
                                                 self._loop).result(timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        else:
            self.ser.close()


def xact(link, cmd, timeout=3.0):
    """Send one command, collect until the '>' prompt. Returns
    (rtt_ms, reads, gaps_ms, text)."""
    link.send((cmd + "\r").encode())
    t0 = time.perf_counter()
    buf = b""
    reads = []
    while True:
        remaining = timeout - (time.perf_counter() - t0)
        if remaining <= 0:
            return (None, len(reads), [], buf.decode(errors="replace"))
        data = link.recv(remaining)
        if not data:
            return (None, len(reads), [], buf.decode(errors="replace"))
        reads.append((time.perf_counter(), data))
        buf += data
        if buf.rstrip().endswith(b">"):
            break
        if b"SEARCHING" in buf and timeout < 20.0:
            # ATSP0 protocol search (6 s on the MIC, longer on some clones):
            # abandoning it early makes every later request STOPPED/restart
            timeout = 20.0
    t1 = reads[-1][0]
    gaps = [(reads[i][0] - reads[i - 1][0]) * 1000 for i in range(1, len(reads))]
    return ((t1 - t0) * 1000, len(reads), gaps, buf.decode(errors="replace"))


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = int(round((p / 100) * (len(s) - 1)))
    return s[k]


def is_bad(text, cmd):
    """A response that is not a clean answer to @p cmd: a timeout, an
    ELM error/interrupt line, or somebody else's data (cross-talk)."""
    want = "4" + cmd[1:4].replace(" ", "")  # 010C -> 410C
    flat = text.replace(" ", "")
    return ("STOPPED" in text or "NO DATA" in text or "?" in text
            or want not in flat)


def run_loop(link, cmd, n, quiet=False, init=INIT, per_cmd=False):
    """The request loop. Returns (summary dict, raw samples).

    @p cmd is one command (repeated @p n times) or a LIST played round
    robin (a Car Scanner style dashboard cycle) for @p n requests in
    total; with n <= 0 a list is played exactly once (tap replay)."""
    for c in init:
        if not c:
            continue
        rtt, reads, gaps, text = xact(link, c, timeout=6.0)
        if not quiet:
            print(f"init {c:8s} -> {rtt if rtt is None else round(rtt, 1)} ms "
                  f"{text.strip()!r}"[:120])

    cmds = [cmd] if isinstance(cmd, str) else list(cmd)
    if n <= 0:
        n = len(cmds)
    rtts, readcounts, allgaps, timeouts, bad = [], [], [], 0, 0
    raw = []
    t_start = time.perf_counter()
    for i in range(n):
        c = cmds[i % len(cmds)]
        at = c.upper().startswith(("AT", "ST", "VT"))
        rtt, reads, gaps, text = xact(link, c, timeout=6.0 if at else 3.0)
        raw.append({"i": i, "cmd": c, "rtt": rtt, "reads": reads,
                    "gaps": gaps, "text": text})
        if rtt is None:
            timeouts += 1
            bad += 1
            continue
        if not at and is_bad(text, c):
            bad += 1
        if at:
            continue  # replayed AT commands don't count as polls
        rtts.append(rtt)
        readcounts.append(reads)
        allgaps.extend(gaps)
    elapsed = time.perf_counter() - t_start

    summary = {
        "target": link.name,
        "cmd": cmd if isinstance(cmd, str) else f"{len(cmds)} cmds",
        "n": n, "ok": len(rtts),
        "timeouts": timeouts, "bad": bad,
        "rate_hz": round(len(rtts) / elapsed, 2) if elapsed else 0,
        "min": round(min(rtts), 1) if rtts else None,
        "p50": round(pct(rtts, 50), 1) if rtts else None,
        "p90": round(pct(rtts, 90), 1) if rtts else None,
        "p95": round(pct(rtts, 95), 1) if rtts else None,
        "max": round(max(rtts), 1) if rtts else None,
        "stdev": round(statistics.pstdev(rtts), 1) if len(rtts) > 1 else 0,
        "stalls_gt150": sum(1 for r in rtts if r > 150),
        "multi_read": sum(1 for r in readcounts if r > 1),
        "gaps_gt30": sum(1 for g in allgaps if g > 30),
    }
    if per_cmd or len(cmds) > 1:
        by = {}
        for x in raw:
            if x["rtt"] is None or x["cmd"].upper().startswith(("AT", "ST", "VT")):
                continue
            by.setdefault(x["cmd"], []).append(x["rtt"])
        summary["per_cmd"] = {
            c: {"n": len(v), "p50": round(pct(v, 50), 1),
                "p95": round(pct(v, 95), 1),
                "bad": sum(1 for x in raw if x["cmd"] == c and x["rtt"] is not None
                           and is_bad(x["text"], c))}
            for c, v in by.items()}
        # dashboard refresh: how often the FIRST command of the cycle
        # comes around (= the app's gauge update rate for that PID)
        lead = next((c for c in cmds
                     if not c.upper().startswith(("AT", "ST", "VT"))), cmds[0])
        firsts = [x for x in raw if x["cmd"] == lead and x["rtt"] is not None]
        summary["cycle_hz"] = round(len(firsts) / elapsed, 2) if elapsed else 0
    return summary, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="WiCAN address (TCP)")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--serial", default="", help="COMx / /dev/ttyUSBx")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--cmd", default="010C")
    ap.add_argument("--hint", action="store_true",
                    help="append the ELM 'expected responses' hint ' 1'")
    ap.add_argument("--cmds", default="",
                    help="comma list played round robin instead of --cmd "
                         "(a dashboard cycle, e.g. 010C,010D,0105,0111); "
                         "--hint applies to each")
    ap.add_argument("--replay", default="", metavar="TAP_LOG",
                    help="play the exact command sequence recorded by "
                         "elm_tcp_tap.py (AT commands included, once; "
                         "--init defaults to nothing)")
    ap.add_argument("--ble", default="", metavar="ADDR",
                    help="BLE ELM dongle address (vLinker etc., bleak)")
    ap.add_argument("--ble-write", default="", help="write char uuid")
    ap.add_argument("--ble-notify", default="", help="notify char uuid")
    ap.add_argument("--label", default="")
    ap.add_argument("--raw", default="")
    ap.add_argument("--init", default=None)
    a = ap.parse_args()
    if not a.host and not a.serial and not a.ble:
        ap.error("give HOST, --serial COMx or --ble ADDR")

    link = Link(host=a.host, port=a.port, serial_port=a.serial or None,
                baud=a.baud, ble=a.ble or None, ble_write=a.ble_write or None,
                ble_notify=a.ble_notify or None)
    if a.replay:
        sys.path.insert(0, __import__("os").path.dirname(__file__))
        from elm_tcp_tap import parse_log, commands
        # the app's stream in order, AT commands and polls interleaved
        cmd = [c for _, c in commands(parse_log(a.replay))]
        n = 0 if a.n == 200 else a.n  # default: play the log once
        init = (a.init or "").split(",")
    else:
        cmd = ([c + (" 1" if a.hint else "") for c in a.cmds.split(",")]
               if a.cmds else a.cmd + (" 1" if a.hint else ""))
        n = a.n
        init = (",".join(INIT) if a.init is None else a.init).split(",")
    summary, raw = run_loop(link, cmd, n, init=init)
    summary["label"] = a.label
    # the older field names the bench parses
    summary["nodata"] = sum(1 for x in raw if "NO DATA" in x["text"])
    print("SUMMARY " + json.dumps(summary))
    hist = {}
    for x in raw:
        if x["rtt"] is not None:
            b = int(x["rtt"] // 25) * 25
            hist[b] = hist.get(b, 0) + 1
    print("HIST " + " ".join(f"{k}:{v}" for k, v in sorted(hist.items())))
    if a.raw:
        with open(a.raw, "w") as f:
            json.dump({"summary": summary, "samples": raw}, f)
    link.close()


if __name__ == "__main__":
    main()
