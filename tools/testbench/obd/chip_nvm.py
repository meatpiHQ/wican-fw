#!/usr/bin/env python3
"""What the OBD chip keeps: a snapshot of every stored cell that can be read, and the difference between two.

The OBD chip of a WiCAN PRO stores settings of its own (its protocol store,
the programmable parameters, the sleep and wake configuration, a user byte and
a 256-byte user area, protocol definitions, wake-up sequences, its identity
strings). Its memory is rated for a limited number of writes and a write cut
short can cost the chip, so a bench that touches the chip has to be able to
say "nothing was written". The firmware counts its own deliberate writes; this
tool reads the chip itself, whoever wrote it.

  snapshot   read every cell below through the DUT's /ws/obd bridge (the app
             path) and print it as JSON, or save it with --out
  diff       two snapshots (files), or one file against the chip as it is now:
             every cell that reads differently. Exit 1 when something differs
  check      two snapshots back to back must be equal: a cell that changes by
             itself is not a stored cell and must come off the list

What is read (nothing is written; every word is a display or a read):
  identity          ATI, AT@1, AT@2, STDI, STI, STMFR, VTI, VTD, VTPROI, VTVERS,
                    VT RD_MAC, VT RD_UDS, VT RDSN
  parameters        ATPPS (every programmable parameter with its on/off flag)
  sleep and wake    STSLCS, VTPPSWS. Not `VT POWERMANAGE`, which was in the
                    list until 2026-11-11: it reports how the chip last went
                    to sleep and woke (`SLEEP: NONE | WAKE: NONE`, then
                    `PWR EN | UART` after the unit's supply was cut for 4 s
                    with no word sent, `CMD | PWR EN` after a sleep word).
                    A status, not a setting
  user byte, area   ATRD, VT RD_EE (the 256 bytes in two reads)
  protocol tables   VT AT_PROTOCOL_ALL, VT ST_PROTOCOL_ALL, VT VT_PROTOCOL_ALL
                    (the definitions and the wake-up sequence each is linked to)
  wake sequences    VT DISP_CAN_WM 1..8, VT DISP_ISO_WM 1..8
  stored protocol   a temporary protocol (ATTP3), an app's reset (ATZ, which
                    the firmware sends as a warm start) and ATDPN; then the
                    same behind ATTP5. The warm start reloads the stored
                    protocol over a temporary AT one, so both reads name the
                    store. Not behind an ST or VT preset (STP 53, VTP 2C3):
                    such a preset SURVIVES the warm start, ATDPN keeps
                    reading `53 [ST]` and says nothing about the store (bench
                    2026-11-11), hence the temporary protocol first

The reset makes the snapshot an app session like any other: it ends the
session the chip had, and AutoPID, when it is on, sends its prelude again
afterwards. Run it between legs, not inside one.

A long answer could arrive short: until the driver's change of 2026-11-11
the device's chip UART lost bytes now and then on an output of 250 bytes and
more (its hardware FIFO ran over; the device counts it in /api/obd_chip
`uart.rx_overflows` and logs `rx overflow (fifo) - input flushed`; found with
this tool, 3 of 24 long reads; the bench for it is chip_uart_burst_bench.py).
Every word is still read with that counter on both sides, and read again when
it moved, for an image before that change; the snapshot says how often that
happened (`reread`).

usage: chip_nvm.py snapshot [--dut localhost:18081] [--out FILE]
       chip_nvm.py diff A.json [B.json] [--dut localhost:18081]
       chip_nvm.py check [--dut localhost:18081]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`)
"""
import http.client
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import wsmin  # noqa: E402

#: (group, word): the words in the order they are sent
WORDS = (
    [("identity", w) for w in ("ATI", "AT@1", "AT@2", "STDI", "STI", "STMFR", "VTI", "VTD", "VTPROI",
                               "VTVERS", "VT RD_MAC", "VT RD_UDS", "VT RDSN")] +
    [("parameters", "ATPPS")] +
    [("sleep and wake", w) for w in ("STSLCS", "VTPPSWS")] +
    [("user byte and area", w) for w in ("ATRD", "VT RD_EE 00, 80", "VT RD_EE 80, 80")] +
    [("protocol tables", w) for w in ("VT AT_PROTOCOL_ALL", "VT ST_PROTOCOL_ALL", "VT VT_PROTOCOL_ALL")] +
    [("wake sequences", "VT DISP_CAN_WM %d" % n) for n in range(1, 9)] +
    [("wake sequences", "VT DISP_ISO_WM %d" % n) for n in range(1, 9)]
)
STORED_PROTOCOL = "stored protocol (ATTPn, ATZ, ATDPN, twice)"


class Chip:
    """The chip through the DUT's /ws/obd bridge: one word, the answer up to
    the prompt, the chip's line ends shown as ' | '."""

    def __init__(self, dut_hp):
        host, _, port = dut_hp.partition(":")
        self.dut_hp = dut_hp
        self.reread = 0
        self.http = None                # ONE connection for the counter reads
        self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=4)
        self.ws.settimeout(0.2)
        self.cmd("", 1.0)

    def overflows(self):
        """the device's count of bytes lost on the chip's UART (None = unreadable)"""
        # kept open across the reads: a snapshot is 80 of them, and through an
        # ssh tunnel that many short connections wedged the tunnel after 25
        # minutes of legs (2026-11-11)
        for _ in (1, 2):
            try:
                if self.http is None:
                    host, _, port = self.dut_hp.partition(":")
                    self.http = http.client.HTTPConnection(host, int(port or 80), timeout=8)
                self.http.request("GET", "/api/obd_chip")
                body = self.http.getresponse().read()
                return json.loads(body.decode()).get("uart", {}).get("rx_overflows")
            except (OSError, ValueError, http.client.HTTPException):
                try:
                    self.http.close()
                except Exception:  # noqa: BLE001
                    pass
                self.http = None
        return None

    def read(self, word, tries=4):
        """`word`'s answer, read again while the device lost bytes of it"""
        text = ""
        for _ in range(tries):
            before = self.overflows()
            text = self.cmd(word)
            if self.overflows() == before:
                return text
            self.reread += 1
            time.sleep(0.3)
            self.cmd("", 1.0)           # whatever is left of the cut answer
        return text

    def cmd(self, text, wait_s=6.0):
        self.ws.pump()
        self.ws.send((text + "\r").encode(), wsmin.OP_BIN)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            frames = self.ws.pump()
            for _, payload in frames:
                buf += payload
            # the prompt is a `>` at the start of a line with nothing behind
            # it; a `>` inside a line is text (STSLCS: `VL_WAKE: OFF, >13.20V
            # FOR 1 s`; until 2026-11-11 this took the first `>` for the end
            # and that answer lost its last two lines)
            if buf == b">" or buf.endswith((b"\r>", b"\n>")):
                buf = buf[:-1]
                break
            if not frames:
                time.sleep(0.02)
        text_out = buf.decode(errors="replace")
        lines = [ln.strip() for ln in text_out.replace("\n", "\r").split("\r")]
        return " | ".join(ln for ln in lines if ln)

    def stored_protocol(self):
        """the protocol in the chip's store, read twice behind two temporary
        protocols; 'NOT STEADY: a / b' when the two reads differ"""
        seen = []
        for n in ("3", "5"):
            self.cmd("ATTP" + n)
            self.cmd("ATZ")
            self.cmd("ATE0")
            seen.append(self.read("ATDPN"))
        return seen[0] if seen[0] == seen[1] else "NOT STEADY: %s / %s" % (seen[0], seen[1])

    def close(self):
        if self.http is not None:
            try:
                self.http.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws.close()


REREAD = [0]                        # answers of the last snapshot() read again


def snapshot(dut_hp):
    """{word: answer} of every cell; the stored protocol last (its reset ends
    the session the reads ran in). REREAD[0] = how many answers had to be
    read again because the device lost bytes of them."""
    chip = Chip(dut_hp)
    try:
        chip.cmd("ATE0")
        cells = {}
        for _, word in WORDS:
            cells[word] = chip.read(word)
        cells[STORED_PROTOCOL] = chip.stored_protocol()
        chip.cmd("ATPC")            # the session this tool opened is none: say so to the chip
    finally:
        REREAD[0] = chip.reread
        chip.close()
    return cells


def diff(a, b):
    """[(word, before, after)] for every cell that reads differently."""
    return [(w, a.get(w), b.get(w)) for w in sorted(set(a) | set(b)) if a.get(w) != b.get(w)]


def show_diff(changes, out=sys.stdout):
    for word, before, after in changes:
        out.write("CHANGED %s\n  before: %s\n  after : %s\n" % (word, before, after))


def main():
    args = sys.argv[1:]

    def opt(name, default):
        return args[args.index(name) + 1] if name in args else default

    dut = opt("--dut", "localhost:18081")
    what = args[0] if args else ""
    files = [a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and args[i - 1] not in ("--dut", "--out")]
    if what == "snapshot":
        cells = snapshot(dut)
        text = json.dumps({"taken": time.strftime("%Y-%m-%d %H:%M:%S"), "reread": REREAD[0], "cells": cells}, indent=1)
        out = opt("--out", "")
        if out:
            open(out, "w", encoding="utf-8").write(text + "\n")
            print("CHIP NVM snapshot: %d cells -> %s (%d read again after a UART overflow)" % (len(cells), out, REREAD[0]))
        else:
            print(text)
        return 0
    if what == "diff" and files:
        a = json.load(open(files[0], encoding="utf-8"))["cells"]
        b = json.load(open(files[1], encoding="utf-8"))["cells"] if len(files) > 1 else snapshot(dut)
        changes = diff(a, b)
        show_diff(changes)
        print("CHIP NVM %s (%d cells, %d changed)" % ("SAME" if not changes else "CHANGED", len(a), len(changes)))
        return 1 if changes else 0
    if what == "check":
        a = snapshot(dut)
        again = REREAD[0]
        time.sleep(1)
        b = snapshot(dut)
        again += REREAD[0]
        changes = diff(a, b)
        show_diff(changes)
        unknown = [w for w, v in a.items() if v in ("?", "")]
        for w in unknown:
            print("note: %s answers %r on this chip" % (w, a[w]))
        print("CHIP NVM CHECK %s (%d cells read twice, %d not steady, %d without an answer, %d read again "
              "after a UART overflow)" % ("PASS" if not changes else "FAIL", len(a), len(changes), len(unknown), again))
        return 1 if changes else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
