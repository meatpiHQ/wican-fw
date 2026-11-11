#!/usr/bin/env python3
"""Which per-bus option words of the OBD chip are RAM, and what the chip keeps  ->  CHIP WORDS DONE / FAIL

The protocol work may set per-bus options on the OBD chip (the K-line's
baud rate and init address, the J1850 in-frame response, the single-wire CAN
transceiver's mode, a CAN bit rate). The rule for it: RAM words only, and
only words the bench has seen NOT survive a reset. The chip's store is rated
for a limited number of writes, so a word that writes it at every connect is
not an option. This bench is that classification: it replaces the
datasheet's wording wherever the two disagree.

Per word, over /ws/obd (the app path), the chip otherwise idle:
  sent        the word with a value that is not the default; the chip's
              answer (`OK`, or `?` = this chip does not take the word here)
  at once     every readable cell (chip_nvm's list: the programmable
              parameters, the sleep configuration, the protocol tables, the
              user area, the wake sequences ...) and three live reads
              (`ATDPN`, `VTPBRD`, `STPBRR`) against the state before the
              word: what the word changed that can be seen
The bit-rate words (`STPBR`, `VTPBR`) answer `?` on a legislated AT protocol
and are taken behind a protocol preset, so the list ends with `ATM0` (the
chip must acknowledge it: no switch with the memory function on), the preset
`STP 53` (MS CAN) as a row of its own, and the two words behind it. No
request is sent, so the memory function has nothing to store.

Then, once for all of them:
  warm start  an app's reset (`ATZ`, which the firmware sends as `ATWS`):
              the cells and the live reads again. A cell that still holds
              what a word put there SURVIVES THE WARM START (the firmware's
              own reset at every software restart)
  hard reset  a real pulse on the chip's reset pin. The bench cannot pulse
              the pin itself, so it makes the bring-up do it: the chip's
              UART is moved to another speed with the volatile `STSBR`
              through the wire probe, the unit is restarted (planned), the
              bring-up's warm start gets no answer at 2 Mbaud, logs `soft
              reset not confirmed; hardware reset` and pulses the pin. The
              chip comes back at its stored speed. A cell that still holds
              what a word put there is KEPT BY THE CHIP

A reset pulse shows that a value is kept, not where: only a power-off shows
that it is stored. For the preset that was done by hand on 2026-11-11: the
unit's supply off for two minutes, the console adapter's line held low so
that it fed nothing, and the chip still read `53 [ST]` after the power-on. A
preset is STORED by the chip, `ATM0` or not. So the bench says "kept", prints
it as a FACT, and fails only when an OPTION word is kept: the preset being
kept is the known finding, and the bench's one `STP 53` and its way back
cost the chip's store two writes a run.

At the end the chip is put back on its stored protocol (`ATTP3`, a warm
start: a temporary AT protocol does not outlive it) and the unit is
restarted once more, so that the firmware's own read of the store is right.

AutoPID must be off (its requests would land between the words). Two
planned restarts, one of them with the slow bring-up (about 8.5 s, one W
line). Two writes of the chip's store (the preset and its removal).

usage: chip_word_class_bench.py [--dut localhost:18081] [--no-reset]
  the DUT through an ssh tunnel (`ssh -N -L 18081:<dut>:80 rpi001`).
About 10 min for the twenty rows.
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
sys.path.insert(0, HERE)
import chip_nvm   # noqa: E402  (the chip through /ws/obd, the readable cells)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
NO_RESET = "--no-reset" in ARGS
PRESET = "protocol preset"
#: (bus it belongs to, the word with a value that is not its default)
WORDS = [
    ("K-line", "ATIB 96"),          # ISO baud 9600 (default 10.4k)
    ("K-line", "ATIIA 13"),         # 5-baud init address (default 33)
    ("K-line", "ATKW0"),            # key word checking off (default on)
    ("K-line", "ATSW 80"),          # wakeup interval (default 92)
    ("K-line", "ATWM 8133F13E"),    # wakeup message
    ("K-line", "ATTA 13"),          # tester address (default F1)
    ("J1850", "ATIFR0"),            # in-frame response off (default auto)
    ("J1850", "ATIFR H"),           # IFR from the header (default from the source)
    ("timing", "ATST 40"),          # response timeout (default 32)
    ("timing", "ATAT2"),            # adaptive timing 2 (default 1)
    ("single-wire CAN", "ATTM 1"),      # transceiver mode
    ("single-wire CAN", "ATTM 3"),
    ("single-wire CAN", "STCSWM 1"),    # the STN name of the same
    ("single-wire CAN", "ATSWCL 1"),    # the tool resistor switch
    ("single-wire CAN", "VTSWRS 1"),    # the load resistor connection
    ("single-wire CAN", "VTSWSC 1"),    # baud rate follows program events
    ("bit rate", "STPBR 250000"),       # on the AT protocol the chip is on: `?`
    (PRESET, "STP 53"),                 # MS CAN by its preset (behind an acknowledged ATM0)
    ("bit rate", "STPBR 250000"),       # the STN word, behind the preset
    ("bit rate", "VTPBR 83333"),        # the vendor's word for it
]
LIVE = ["ATDPN", "VTPBRD", "STPBRR"]
CHECKS = []
PLANNED = [0]


def now():
    return time.strftime("%H:%M:%S")


def say(text):
    print("%s %s" % (now(), text), flush=True)


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:400] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(path, method="GET", body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def guard():
    st, oc = http("/api/obd_chip")
    return oc.get("eeprom_guard", {}) if isinstance(oc, dict) else {}


def state(chip):
    """every readable cell plus the live reads, as one dict"""
    cells = {w: chip.read(w) for _, w in chip_nvm.WORDS}
    for w in LIVE:
        cells["live " + w] = chip.cmd(w)
    return cells


def changed(a, b):
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def brief(changes):
    out = []
    for k, x, y in changes:
        if len(str(x)) + len(str(y)) < 60:
            out.append("%s: %s -> %s" % (k, x, y))
        else:
            # a long cell (a table): the first place it differs
            i = next((n for n, (p, q) in enumerate(zip(str(x), str(y))) if p != q), min(len(str(x)), len(str(y))))
            out.append("%s: at %d ..%s.. -> ..%s.." % (k, i, str(x)[max(0, i - 12):i + 24], str(y)[max(0, i - 12):i + 24]))
    return "; ".join(out)


def owners(rows, base, later):
    """{row index: [cells]}: for every cell that differs from `base` in
    `later`, the last row that put that very value there. Second: the cells
    nobody's value explains."""
    own, orphan = {}, []
    for cell, _, value in changed(base, later):
        hit = [i for i, row in enumerate(rows) if any(k == cell and after == value for k, _, after in row[3])]
        if hit:
            own.setdefault(hit[-1], []).append(cell)
        else:
            orphan.append(cell)
    return own, orphan


def restart():
    """a planned restart; the chip driver's lines of the new boot"""
    st, h = http("/api/restart/history")
    b0 = h.get("boot_count") if isinstance(h, dict) else None
    http("/api/logs/ring", "DELETE")
    http("/api/restart", "POST", {})
    PLANNED[0] += 1
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < 150:
        st, h = http("/api/restart/history", timeout=4)
        st2, oc = http("/api/obd_chip", timeout=4)
        if (isinstance(h, dict) and h.get("boot_count") not in (None, b0) and
                isinstance(oc, dict) and oc.get("ready")):
            break
        time.sleep(1)
    time.sleep(3)
    st, ring = http("/api/logs/ring", timeout=20)
    text = ring if isinstance(ring, str) else json.dumps(ring)
    return [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in text.splitlines() if " obd_chip: " in ln]


def hard_reset():
    """the bring-up's reset pulse, provoked: True when the ring says so"""
    st, r = http("/api/obd_chip/probe", "POST", {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": "STSBR115200"})
    if st != 202:
        say("the wire probe refused STSBR115200: %s %s" % (st, r))
        return False
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        st, pr = http("/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
            break
    lines = restart()
    pulsed = [ln for ln in lines if "soft reset not confirmed; hardware reset" in ln]
    for ln in lines:
        if "hardware reset" in ln or "started: chip" in ln or "bring-up done" in ln:
            say("  ring: %s" % ln[-120:])
    return bool(pulsed)


def main():
    st, ap = http("/api/settings/autopid")
    st2, oc = http("/api/obd_chip")
    if not (isinstance(ap, dict) and isinstance(oc, dict) and oc.get("ready")):
        print("CHIP WORDS FAIL (the unit does not answer at %s, or its chip is not ready)" % DUT_HP)
        return 1
    if not check("AutoPID is off (its requests would land between the words)", not ap.get("enabled")):
        print("CHIP WORDS FAIL (switch AutoPID off for the run)")
        return 1
    g0 = oc.get("eeprom_guard", {})
    st, h0 = http("/api/restart/history")
    say("%d rows; the chip %s, store %s" % (len(WORDS), (oc.get("fw") or {}).get("chip"), g0.get("stored_protocol")))

    chip = chip_nvm.Chip(DUT_HP)
    chip.cmd("ATE0")
    base = state(chip)
    say("the state before the first word: %d cells; live %s" % (
        len(base), ", ".join("%s=%s" % (w, base["live " + w]) for w in LIVE)))
    rows = []                       # [bus, word, answer, [(cell, before, after)]]
    prev = base
    skip_rest = False               # ATM0 refused: no preset, and nothing behind it
    for bus, word in WORDS:
        if bus == PRESET and not skip_rest:
            skip_rest = not check("ATM0 acknowledged ahead of the preset (no switch with the memory function on)",
                                  "OK" in chip.cmd("ATM0"))
        if skip_rest:
            say("  %-16s %-16s -> not sent" % (bus, word))
            rows.append([bus, word, "not sent", []])
            continue
        b0 = guard().get("blocked") or 0
        answer = chip.cmd(word, 3.0)
        if (guard().get("blocked") or 0) != b0:
            answer = "REFUSED"      # the firmware's guard answered, the chip never saw the word
        cur = state(chip)
        ch = changed(prev, cur)
        rows.append([bus, word, answer, ch])
        say("  %-16s %-16s -> %-8s %s" % (bus, word, answer[:8], brief(ch) if ch else "(no readable cell moved)"))
        prev = cur

    time.sleep(2)
    say("warm start: ATZ -> %r" % chip.cmd("ATZ", 4.0))
    chip.cmd("ATE0")
    warm = state(chip)
    left_warm = changed(base, warm)
    say("after the warm start, against the state before the first word: %s" % (brief(left_warm) if left_warm else "nothing differs"))
    warm_own, warm_orphan = owners(rows, base, warm)
    chip.close()

    hard = None
    hard_own, hard_orphan = {}, []
    if not NO_RESET:
        check("the bring-up pulsed the chip's reset pin (`soft reset not confirmed; hardware reset`)", hard_reset())
        chip = chip_nvm.Chip(DUT_HP)
        chip.cmd("ATE0")
        hard = state(chip)
        chip.close()
        left_hard = changed(base, hard)
        say("after the hardware reset, against the state before the first word: %s" % (brief(left_hard) if left_hard else "nothing differs"))
        hard_own, hard_orphan = owners(rows, base, hard)

    # the chip back on its stored protocol: a temporary AT protocol does not
    # outlive a warm start, a preset does
    chip = chip_nvm.Chip(DUT_HP)
    chip.cmd("ATE0")
    if chip.cmd("ATDPN") != base["live ATDPN"]:
        chip.cmd("ATTP3")
        chip.cmd("ATZ", 4.0)
        chip.cmd("ATE0")
    back = chip.cmd("ATDPN")
    chip.cmd("ATPC")
    chip.close()
    check("the chip on its protocol as found at the end", back == base["live ATDPN"], "%s, as found %s" % (back, base["live ATDPN"]))
    restart()                       # the firmware reads the store again
    g1 = guard()

    print()
    print("%-16s %-16s %-9s %s" % ("bus", "word", "answer", "class"))
    kept_words = []
    for i, (bus, word, answer, ch) in enumerate(rows):
        cells = [k for k, _, _ in ch]
        if answer == "REFUSED":
            cls = "refused by the firmware's guard (the chip never saw it)"
        elif answer == "not sent":
            cls = "not sent (ATM0 was not acknowledged)"
        elif "?" in answer:
            cls = "not taken by the chip here"
        elif i in hard_own:
            cls = "KEPT across the hardware reset (%s)" % ", ".join("%s = %s" % (k, hard[k]) for k in hard_own[i])
            if bus == PRESET:
                print("%s FACT the preset %s is kept by the chip across a warm start and a hardware reset "
                      "(memory function off, no request sent)" % (now(), word), flush=True)
            else:
                kept_words.append(word)
        elif i in warm_own:
            cls = "RAM, survives the warm start (%s)%s" % (
                ", ".join("%s = %s" % (k, warm[k]) for k in warm_own[i]), "" if NO_RESET else ", gone after the hardware reset")
        elif cells:
            cls = "RAM (changed %s; gone after the warm start)" % ", ".join(cells)
        else:
            cls = "no readable cell moved"
        print("%-16s %-16s %-9s %s" % (bus, word, answer[:9], cls))
    print()

    if not NO_RESET:
        check("no option word is kept across the hardware reset", not kept_words, ", ".join(kept_words))
        check("no change is left that no word explains", not warm_orphan and not hard_orphan,
              "after the warm start %s, after the hardware reset %s" % (warm_orphan, hard_orphan))
    check("the chip's stored protocol as found, no protocol save", g1.get("stored_protocol") == g0.get("stored_protocol") and
          g1.get("protocol_saves") == 0, "%s -> %s, protocol_saves %s" % (
              g0.get("stored_protocol"), g1.get("stored_protocol"), g1.get("protocol_saves")))
    st, h1 = http("/api/restart/history")
    check("the unit rode through", isinstance(h0, dict) and isinstance(h1, dict) and
          h1.get("boot_count") == h0.get("boot_count") + PLANNED[0] and
          h1.get("unexpected_resets") == h0.get("unexpected_resets"),
          "boot %s -> %s with %d planned" % ((h0 or {}).get("boot_count"), (h1 or {}).get("boot_count"), PLANNED[0]))
    failed = [n for n, ok in CHECKS if not ok]
    print("CHIP WORDS %s (%d rows, %d survive the warm start, %d kept across the hardware reset, %d checks, %d failed, "
          "%d planned restarts)" % ("DONE" if not failed else "FAIL", len(rows), len(warm_own), len(hard_own),
                                    len(CHECKS), len(failed), PLANNED[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
