#!/usr/bin/env python3
"""A long output of the OBD chip arrives whole  ->  CHIP UART BURST PASS / FAIL

The OBD chip talks to the WiCAN PRO's processor at 2,000,000 baud: a byte
every 5 us, into a hardware receive FIFO of 128 bytes. A short OBD reply fits
into that FIFO whole. A long output (the chip's parameter summary, its
protocol tables, its user area: 250 to 1600 bytes in one unbroken burst) has
to be taken out of the FIFO several times while it is still arriving, and
every one of those hand-overs has to start before the FIFO is full. When one
starts too late the device loses up to a FIFO of bytes, drops what it had
buffered, counts it (`uart.rx_overflows` of GET /api/obd_chip) and logs
`rx overflow (fifo) - input flushed`: the answer arrives cut.

Found on 2026-11-11 with chip_nvm.py (3 of 24 long reads cut on the image of
that day: the hand-over started only at 120 bytes, 8 bytes or 40 us before
the end of the FIFO, and the driver's interrupt waited behind the WiFi ones
on the same core). Since then the driver is called at 32 bytes and its
interrupt lives on the other core. This bench is the reproduction and the
regression:

  boots      --boots planned restarts: the bring-up reads two of the long
             words itself at every boot (STSLCS and ATPPS, and what it reads
             there decides whether the chip's sleep configuration and its
             PP 0F are written again), so after each one: no overflow
             counted, the peak in bounds, no warning of the chip driver
             about those reads
  reference  each long word read until two answers agree byte for byte with
             no overflow counted under them
  quiet      --rounds times the seven long words through /ws/obd (the app
             path), nothing else asked of the unit but its counters
  busy       the same while a second client asks the unit's HTTP routes as
             fast as they answer (WiFi and the web server at work)

It passes when no overflow was counted and every answer was whole, and, on
an image that reports them, when two margins held:
  the peak   `uart.rx_fifo_peak`, the fullest any hand-over found the FIFO,
             at or under --peak-max (96: a quarter of the FIFO, 160 us,
             left). The hand-over starts at the driver's threshold (32), so
             peak minus threshold is how late the latest one started, in
             bytes of 5 us. It is ONE event: 48 to 72 on the bench unit
  the share  `uart.rx_late` of `uart.rx_handovers`, the hand-overs that
             started 40 us or more behind their interrupt, at or under
             --late-max per cent (0.5). This is what tells where the
             interrupt lives: 0.07 % on the app core, 2.2 % behind the WiFi
             interrupts on core 0, where the answers of a run can all be
             whole and the margin still be thin

Nothing is written to the chip: every word is a display. The words do not
open a vehicle protocol and no setting of the unit is changed. The peak is
counted since the boot, so the restarts of the boots leg also make it the
peak of this run alone (--boots 0: no restart, the peak is whatever the
unit has seen since it started).

usage: chip_uart_burst_bench.py [--dut localhost:18081] [--boots 5] [--rounds 20]
                                [--peak-max 96] [--late-max 0.5]
                                [--only boots,quiet,busy]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`).
About 4 min: 5 planned restarts, 280 long reads.
"""
import http.client
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import chip_nvm   # noqa: E402  (the chip through /ws/obd)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
BOOTS = int(arg("--boots", "5"))
ROUNDS = int(arg("--rounds", "20"))
PEAK_MAX = int(arg("--peak-max", "96"))
LATE_MAX = float(arg("--late-max", "0.5"))   # per cent of the hand-overs
ONLY = [s for s in arg("--only", "boots,quiet,busy").split(",") if s]
#: a warning of the chip driver about the bring-up's long reads
BOOT_WARN = re.compile(r"rx overflow|STSLCS|ATPPS|PP 0F")
FIFO = 128                          # the processor's UART receive FIFO, bytes
#: the chip's long displays (bytes of the answer on V2.3.24: 515, 302, 413, 413, 392, 1199, 1595)
WORDS = ["ATPPS", "STSLCS", "VT RD_EE 00, 80", "VT RD_EE 80, 80",
         "VT AT_PROTOCOL_ALL", "VT ST_PROTOCOL_ALL", "VT VT_PROTOCOL_ALL"]
CHECKS = []
RESTARTS = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


class Unit:
    """The unit's HTTP routes over ONE connection (a run asks its counters a
    few hundred times; that many short connections wedge an ssh tunnel)."""

    def __init__(self, dut_hp):
        self.host, _, port = dut_hp.partition(":")
        self.port = int(port or 80)
        self.conn = None

    def get(self, path, method="GET", timeout=10):
        for _ in (1, 2):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
                self.conn.request(method, path, body=b"{}" if method == "POST" else None,
                                  headers={"Content-Type": "application/json"} if method == "POST" else {})
                body = self.conn.getresponse().read().decode(errors="replace")
                try:
                    return json.loads(body)
                except ValueError:
                    return body
            except (OSError, http.client.HTTPException):
                self.close()
        return None

    def uart(self):
        oc = self.get("/api/obd_chip")
        return oc.get("uart", {}) if isinstance(oc, dict) else {}

    def ready(self):
        # not the claim: with AutoPID on the chip is claimed for a request
        # most of the time, and AutoPID yields to this bench's words
        oc = self.get("/api/obd_chip", timeout=4)
        return isinstance(oc, dict) and bool(oc.get("ready"))

    def history(self):
        h = self.get("/api/restart/history")
        return h if isinstance(h, dict) else {}

    def ring(self):
        """the log ring's lines of the chip driver, colours off"""
        text = self.get("/api/logs/ring", timeout=20)
        text = text if isinstance(text, str) else json.dumps(text)
        lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in text.splitlines()]
        return [ln for ln in lines if " obd_chip: " in ln]

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
        self.conn = None


class Load(threading.Thread):
    """A second client: GET /api/status as fast as the unit answers."""

    def __init__(self, dut_hp):
        super().__init__(daemon=True)
        self.unit = Unit(dut_hp)
        self.stop = threading.Event()
        self.asked = 0

    def run(self):
        while not self.stop.is_set():
            if self.unit.get("/api/status") is not None:
                self.asked += 1
        self.unit.close()


def restart(unit):
    b0 = unit.history().get("boot_count")
    unit.get("/api/restart", "POST")
    RESTARTS[0] += 1
    unit.close()
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        if unit.history().get("boot_count") not in (None, b0) and unit.ready():
            return True
        time.sleep(1)
    return False


def boots(unit, has_peak):
    """BOOTS planned restarts; after each: (overflows, peak, the driver's warnings about its long reads)"""
    seen = []
    for n in range(BOOTS):
        if not restart(unit):
            check("boot %d: the unit came back with its chip up" % (n + 1), False)
            break
        u = unit.uart()
        lines = unit.ring()
        warned = [ln[-110:] for ln in lines if ln.startswith("W ") and BOOT_WARN.search(ln)]
        took = [ln for ln in lines if "bring-up done in" in ln]
        seen.append((u.get("rx_overflows") or 0, u.get("rx_fifo_peak") or 0, warned))
        say("  boot %d: overflows %s, peak %s, %s%s" % (
            n + 1, u.get("rx_overflows"), u.get("rx_fifo_peak", "not reported"),
            took[-1].split("obd_chip: ")[-1] if took else "no bring-up line in the ring",
            "".join("\n      " + w for w in warned)))
    if len(seen) == BOOTS:
        check("no overflow under the bring-up's own long reads, %d boots" % BOOTS,
              all(s[0] == 0 for s in seen), "%d boot(s) with one" % sum(1 for s in seen if s[0]))
        check("no warning of the chip driver about those reads", all(not s[2] for s in seen),
              "%d boot(s) with one" % sum(1 for s in seen if s[2]))
        if has_peak:
            worst = max(s[1] for s in seen)
            check("the bring-up never found the FIFO fuller than %d of %d bytes" % (PEAK_MAX, FIFO),
                  0 < worst <= PEAK_MAX, "peaks %s" % " ".join(str(s[1]) for s in seen))


def references(chip, unit):
    """{word: the whole answer}: two reads that agree, no overflow under them"""
    ref = {}
    for word in WORDS:
        last = None
        for _ in range(6):
            before = unit.uart().get("rx_overflows")
            text = chip.cmd(word)
            if unit.uart().get("rx_overflows") != before:
                chip.cmd("", 1.0)
                last = None
                continue
            if text and text == last:
                ref[word] = text
                break
            last = text
    return ref


def leg(name, chip, unit, ref, rows):
    """ROUNDS times every word; rows[(name, word)] = [reads, cut, overflows, bytes, slowest s]"""
    for _ in range(ROUNDS):
        for word in WORDS:
            before = unit.uart().get("rx_overflows") or 0
            t0 = time.time()
            text = chip.cmd(word)
            took = time.time() - t0
            lost = (unit.uart().get("rx_overflows") or 0) - before
            row = rows.setdefault((name, word), [0, 0, 0, len(ref[word]), 0.0])
            row[0] += 1
            row[1] += 0 if text == ref[word] else 1
            row[2] += lost
            row[4] = max(row[4], took)
            if lost or text != ref[word]:
                say("  %s %s: %d of %d bytes, %d overflow(s), %.2f s" % (name, word, len(text), len(ref[word]), lost, took))
                time.sleep(0.3)
                chip.cmd("", 1.0)   # whatever is left of the cut answer


def main():
    unit = Unit(DUT_HP)
    st = unit.get("/api/status")
    if not isinstance(st, dict) or not unit.ready():
        print("CHIP UART BURST FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    has_peak = "rx_fifo_peak" in unit.uart()
    say("unit: version %s, boot %s, the image %s the FIFO peak" % (
        st.get("version"), st.get("boot_count"), "reports" if has_peak else "does not report"))
    h0 = unit.history()
    if "boots" in ONLY and BOOTS > 0:
        boots(unit, has_peak)
    u0 = unit.uart()
    say("counters at the start: %s" % json.dumps(u0))

    chip = chip_nvm.Chip(DUT_HP)
    rows = {}
    try:
        ref = references(chip, unit)
        check("every long word has a steady answer", len(ref) == len(WORDS),
              "missing: %s" % ", ".join(w for w in WORDS if w not in ref) if len(ref) != len(WORDS) else
              ", ".join("%s %d" % (w, len(ref[w])) for w in WORDS))
        if len(ref) != len(WORDS):
            raise SystemExit(1)
        if "quiet" in ONLY:
            leg("quiet", chip, unit, ref, rows)
        if "busy" in ONLY:
            load = Load(DUT_HP)
            load.start()
            try:
                leg("busy", chip, unit, ref, rows)
            finally:
                load.stop.set()
                load.join(15)
            check("the second client was served during the busy leg", load.asked > ROUNDS,
                  "%d status requests answered" % load.asked)
    except SystemExit:
        pass
    finally:
        chip.close()

    u1 = unit.uart()
    h1 = unit.history()
    print()
    print("%-6s %-20s %6s %6s %5s %9s %8s" % ("leg", "word", "bytes", "reads", "cut", "overflows", "slowest"))
    total = [0, 0, 0]
    for (name, word), (reads, cut, lost, size, slow) in rows.items():
        print("%-6s %-20s %6d %6d %5d %9d %6.2f s" % (name, word, size, reads, cut, lost, slow))
        total = [total[0] + reads, total[1] + cut, total[2] + lost]
    print()
    counted = (u1.get("rx_overflows") or 0) - (u0.get("rx_overflows") or 0)
    check("no overflow counted on the chip's UART", counted == 0 and total[2] == 0,
          "%d counted over %d long reads" % (counted, total[0]))
    check("every long answer arrived whole", total[0] > 0 and total[1] == 0,
          "%d of %d cut" % (total[1], total[0]))
    if has_peak:
        peak = u1.get("rx_fifo_peak") or 0
        check("the FIFO was never found fuller than %d of %d bytes" % (PEAK_MAX, FIFO), 0 < peak <= PEAK_MAX,
              "peak %d: %d bytes or %d us left before a loss" % (peak, FIFO - peak, (FIFO - peak) * 5))
    if "rx_handovers" in u1:
        passes = (u1.get("rx_handovers") or 0) - (u0.get("rx_handovers") or 0)
        late = (u1.get("rx_late") or 0) - (u0.get("rx_late") or 0)
        share = 100.0 * late / passes if passes else 0.0
        check("at most %.1f %% of the hand-overs started 40 us or more late" % LATE_MAX,
              passes > 0 and share <= LATE_MAX, "%d of %d, %.2f %%" % (late, passes, share))
    else:
        say("note: this image does not report uart.rx_fifo_peak; the margin is not measured")
    check("the unit rode through", h1.get("boot_count") == (h0.get("boot_count") or 0) + RESTARTS[0] and
          h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned, unexpected %s -> %s" % (
              h0.get("boot_count"), h1.get("boot_count"), RESTARTS[0],
              h0.get("unexpected_resets"), h1.get("unexpected_resets")))
    unit.close()
    failed = [n for n, ok in CHECKS if not ok]
    say("counters at the end: %s" % json.dumps(u1))
    print("CHIP UART BURST %s (%d long reads, %d cut, %d overflows, %d checks, %d failed, %d planned restart%s)" % (
        "PASS" if not failed else "FAIL", total[0], total[1], counted, len(CHECKS), len(failed),
        RESTARTS[0], "" if RESTARTS[0] == 1 else "s"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
