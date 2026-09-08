#!/usr/bin/env python3
"""ATMA capture leg (runs ON the bench Pi, the ELM app's seat): opens the
WiCAN's obd0 TCP port, turns headers + spaces on, starts ATMA, records the
monitor stream for N seconds, stops it with the SPACE byte (never CR) and
prints one `RESULT {json}` line:

  lines, bytes, secs, lines_per_s, bytes_per_s, malformed (lines that are
  not `<id> <b1> .. <bn>[ <DATA ERROR]`), ids {id: count}, first/last
  timestamps, prompt_back (the '>' returned after the stop byte), and -
  with --seq ID - the counter analysis for frames whose first 4 data bytes
  carry a big-endian sequence number: seq_first, seq_last, seq_count,
  seq_missing, seq_gaps (first 10), seq_dupes.

usage: atma_capture.py HOST [--port 35000] [--secs 10] [--seq 0x123]
                       [--raw FILE] [--no-headers]
"""
import argparse
import json
import re
import socket
import sys
import time

LINE_RE = re.compile(r"^([0-9A-F]{3}|[0-9A-F]{8}) ((?:[0-9A-F]{2} ?){1,8})"
                     r"\s*(<DATA ERROR)?\s*$")


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

    def recv(self, timeout, size=65536):
        if self.kind == "tcp":
            self.sock.settimeout(timeout)
            try:
                return self.sock.recv(size)
            except socket.timeout:
                return b""
        self.ser.timeout = timeout
        d = self.ser.read(1)
        if d:
            d += self.ser.read(min(size, self.ser.in_waiting))
        return d

    def close(self):
        if self.kind == "tcp":
            self.sock.close()
        else:
            self.ser.close()


def xact(link, cmd, timeout=3.0):
    link.send((cmd + "\r").encode())
    t0 = time.time()
    buf = b""
    while time.time() - t0 < timeout:
        d = link.recv(max(0.05, timeout - (time.time() - t0)), 4096)
        if not d:
            break
        buf += d
        if buf.rstrip().endswith(b">"):
            break
    return buf.decode(errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="WiCAN address (TCP)")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--serial", default="", help="COMx of a USB ELM adapter")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("--seq", default="", help="CAN id (hex) whose data "
                    "bytes 0..3 are a big-endian frame counter")
    ap.add_argument("--raw", default="")
    ap.add_argument("--no-headers", action="store_true")
    a = ap.parse_args()
    if not a.host and not a.serial:
        ap.error("give HOST or --serial COMx")

    s = Link(host=a.host, port=a.port, serial_port=a.serial or None,
             baud=a.baud)
    time.sleep(0.3)
    xact(s, "", timeout=0.5)   # drain / sync the prompt
    init = {}
    for c in ["ATE0", "ATL0", "ATS1", "ATH0" if a.no_headers else "ATH1",
              "ATCAF0"]:
        init[c] = xact(s, c).strip()

    # ATMA: no prompt comes back until the stop byte
    s.send(b"ATMA\r")
    t0 = time.time()
    chunks = []
    total = 0
    while time.time() - t0 < a.secs:
        d = s.recv(0.5)
        if not d:
            continue
        chunks.append((time.time(), d))
        total += len(d)
    t_end = time.time()
    s.send(b" ")              # SPACE stops the monitor (CR would repeat ATMA)
    tail = b""
    t1 = time.time()
    while time.time() - t1 < 3.0:
        d = s.recv(0.5)
        if not d:
            break
        tail += d
        if tail.rstrip().endswith(b">"):
            break
    s.close()

    data = b"".join(d for _, d in chunks)
    text = data.decode(errors="replace")
    if text.startswith("ATMA"):
        text = text[4:]
    lines = [ln.strip() for ln in text.split("\r") if ln.strip()]
    ids = {}
    malformed = []
    seqs = []
    seq_id = a.seq.upper().replace("0X", "").zfill(3) if a.seq else ""
    for ln in lines:
        m = LINE_RE.match(ln)
        if not m:
            malformed.append(ln)
            continue
        cid = m.group(1)
        ids[cid] = ids.get(cid, 0) + 1
        if seq_id and cid.endswith(seq_id):
            b = m.group(2).split()
            if len(b) >= 4:
                seqs.append(int("".join(b[:4]), 16))
    # the last line may be cut by the stop: do not count a trailing partial
    # line as malformed
    if malformed and lines and malformed[-1] == lines[-1]:
        malformed = malformed[:-1]

    secs = t_end - t0
    res = {
        "lines": len(lines), "bytes": total, "secs": round(secs, 2),
        "lines_per_s": round(len(lines) / secs, 1) if secs else 0,
        "bytes_per_s": round(total / secs) if secs else 0,
        "malformed": len(malformed), "malformed_first": malformed[:5],
        "ids": dict(sorted(ids.items(), key=lambda kv: -kv[1])[:8]),
        "reads": len(chunks),
        "prompt_back": tail.rstrip().endswith(b">"),
        "init": init,
    }
    if seq_id:
        uniq = sorted(set(seqs))
        gaps = []
        missing = 0
        for i in range(1, len(uniq)):
            d = uniq[i] - uniq[i - 1]
            if d > 1:
                missing += d - 1
                if len(gaps) < 10:
                    gaps.append([uniq[i - 1], uniq[i]])
        res.update({
            "seq_id": seq_id, "seq_count": len(seqs),
            "seq_first": uniq[0] if uniq else None,
            "seq_last": uniq[-1] if uniq else None,
            "seq_missing": missing, "seq_gaps": gaps,
            "seq_dupes": len(seqs) - len(uniq),
            "seq_out_of_order": sum(1 for i in range(1, len(seqs))
                                    if seqs[i] < seqs[i - 1]),
        })
    print("RESULT " + json.dumps(res))
    if a.raw:
        with open(a.raw, "wb") as f:
            f.write(data)


if __name__ == "__main__":
    main()
