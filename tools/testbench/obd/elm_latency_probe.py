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
                 baud=115200):
        self.kind = "serial" if serial_port else "tcp"
        if serial_port:
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

    def drain(self):
        try:
            if self.kind == "tcp":
                self.sock.settimeout(0.3)
                self.sock.recv(4096)
            else:
                self.ser.timeout = 0.3
                self.ser.read(4096)
        except (socket.timeout, OSError):
            pass

    def send(self, data):
        if self.kind == "tcp":
            self.sock.sendall(data)
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
        self.ser.timeout = timeout
        data = self.ser.read(1)
        if data:
            data += self.ser.read(self.ser.in_waiting)
        return data

    def close(self):
        if self.kind == "tcp":
            self.sock.close()
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


def run_loop(link, cmd, n, quiet=False, init=INIT):
    """The request loop. Returns (summary dict, raw samples)."""
    for c in init:
        if not c:
            continue
        rtt, reads, gaps, text = xact(link, c, timeout=6.0)
        if not quiet:
            print(f"init {c:8s} -> {rtt if rtt is None else round(rtt, 1)} ms "
                  f"{text.strip()!r}"[:120])

    rtts, readcounts, allgaps, timeouts, bad = [], [], [], 0, 0
    raw = []
    t_start = time.perf_counter()
    for i in range(n):
        rtt, reads, gaps, text = xact(link, cmd)
        raw.append({"i": i, "rtt": rtt, "reads": reads, "gaps": gaps,
                    "text": text})
        if rtt is None:
            timeouts += 1
            bad += 1
            continue
        if is_bad(text, cmd):
            bad += 1
        rtts.append(rtt)
        readcounts.append(reads)
        allgaps.extend(gaps)
    elapsed = time.perf_counter() - t_start

    summary = {
        "target": link.name, "cmd": cmd, "n": n, "ok": len(rtts),
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
    ap.add_argument("--label", default="")
    ap.add_argument("--raw", default="")
    ap.add_argument("--init", default=",".join(INIT))
    a = ap.parse_args()
    if not a.host and not a.serial:
        ap.error("give HOST or --serial COMx")

    link = Link(host=a.host, port=a.port, serial_port=a.serial or None,
                baud=a.baud)
    cmd = a.cmd + (" 1" if a.hint else "")
    summary, raw = run_loop(link, cmd, a.n, init=a.init.split(","))
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
