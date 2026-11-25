#!/usr/bin/env python3
"""UDS console bench  ->  UDS CONSOLE PASS / FAIL

The report (2026-11-26, 6.0.0-beta.15): "the uds command cuts the response
at about 80 bytes". Seven faults stood behind a long UDS answer, in three
places, and each place has a leg here:

  1. The console. The `uds` command printed its answer with one
     cmdline_printf, whose 256-byte buffer ended the line after the 84th
     byte, without the `[transport, ms]` behind it; the command itself
     showed 128 bytes at most and an answer over 512 bytes was an error.
     And the console kept 11 words of a line and dropped the rest without a
     message: a request typed as bytes (`uds -t 7E0 -r 7E8 23 14 00 00 00
     00 C8`) went out without its last byte, and a request over the 64
     bytes typed as one `22:F1:..` word went out as its first 64.
  2. The native path (WiCAN's own CAN controller). P2, 250 ms, was the time
     for the whole answer: one of 1024 bytes was still arriving when it ran
     out, ESP_ERR_TIMEOUT.
  3. The OBD chip. An AutoPID row's `ATFCSH..;ATFCSM1` stayed in effect
     under the UDS setup: the chip sent its flow control to the row's ECU,
     the asked ECU sent its first frame and nothing more. A cut answer was
     asked for again 0.9 ms later, while the ECU still sent the rest of the
     first, and was cut again. The reply buffer held about 2290 bytes of
     answer. And the parser refused the one message of 4095 bytes.

The ECU is the simulator's own on 7E0/7E8. Its synthetic memory (the byte
at address A is A & 0xFF) gives an answer of any size, checked to the byte
(`23 14 <addr> <size>`, `23 24 ..` above 255), and its WriteMemoryByAddress
answers `7D` only when every byte of the request arrived (`3D 14 ..`).

Legs:
  L0 preflight: the unit and /ws/cli answer, nobody holds the chip, the
     tables and the UDS settings are saved, the ECU serves its memory.
  L1 the answer whole: the same request through POST /api/uds/request and
     through the console command, answers of 7 to 4095 bytes; the console's
     bytes are the route's, the line ends in `[<transport>, N ms]`, and the
     console's output counters lost nothing.
  L2 the request as typed: 7 and 64 bytes with every byte a word of its
     own, `0x` words, one `:` word; 65 bytes and a 70-byte word are refused
     and nothing goes to the ECU.
  L3 the OBD chip behind a row that moved the flow control: one custom row
     with `ATFCSH7E4;ATFCSD300000;ATFCSM1` polled every 250 ms, the UDS
     transport on the chip (a planned restart when it was not). With the
     exclusive switch off the poller writes to the chip between two
     requests: 20 multi-frame reads, each whole. With it on: answers of 20
     to 4095 bytes, and the console command. The row keeps its value.
  L4 restore: the tables, the exclusive switch, the UDS settings (a planned
     restart when they had to change).

usage: uds_console_bench.py [--dut localhost:18081] [--skip-chip]
  --skip-chip drops L3 (no restart then).
Needs: the main firmware on the unit on the bench AP with its `cli` bridge
to /ws/cli, the ECU simulator answering 7E0 with UDS on, Automate on (L3
switches it on with a planned restart when it was off). About 3 min, up to
two planned restarts (four when Automate was off). No console port, no
PCAN, no write of the chip's memory.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

import websocket

TX, RX = "7E0", "7E8"
ROW = "UDSBENCH_COOLANT"
ROW_INIT = "ATSH7E0;ATCRA7E8;ATFCSH7E4;ATFCSD300000;ATFCSM1"
L1_SIZES = [7, 20, 84, 85, 128, 129, 201, 512, 513, 1024, 4095]
CHIP_SIZES = [20, 61, 201, 1024, 2400, 4095]  # 2400: 8 KB of the chip's text


def http(base, method, path, body=None, timeout=40):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def get_json(base, path):
    st, body = http(base, "GET", path)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def console(dut, line, wait_s=25.0):
    """One line over /ws/cli; the text up to the prompt."""
    try:
        ws = websocket.create_connection("ws://%s/ws/cli" % dut, timeout=8)
    except Exception as exc:  # noqa: BLE001
        return "<ws: %s>" % exc
    ws.send(line + "\n")
    ws.settimeout(0.5)
    out = b""
    end = time.time() + wait_s
    while time.time() < end:
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:  # noqa: BLE001
            break
        if frame is None:
            break
        out += frame.encode() if isinstance(frame, str) else frame
        if out.endswith(b"wican> "):
            break
    ws.close()
    return out.decode("ascii", "replace")


def answer_line(text):
    """The console's `<- XX XX ..  [transport, N ms]` -> (bytes, tail)."""
    line = next((l for l in text.splitlines() if l.startswith("<- ")), None)
    if line is None:
        return None, ""
    hexpart, _, tail = line[3:].partition("  [")
    # a line that was cut ends in a stray "]": the bytes in front of it count
    data = [int(x, 16) for x in hexpart.split()
            if re.fullmatch(r"[0-9A-Fa-f]{2}", x)]
    return data, ("[" + tail) if tail else ""


def route(base, data):
    st, body = http(base, "POST", "/api/uds/request",
                    {"tx_id": TX, "rx_id": RX, "data": data})
    try:
        return json.loads(body)
    except ValueError:
        return {"ok": False, "error": "%s %s" % (st, body[:80])}


def route_bytes(r):
    return [int(x, 16) for x in (r.get("response") or "").split()]


def memory_request(size):
    """An answer of @size bytes: 63 + the first size - 1 bytes of memory."""
    n = size - 1
    if n < 256:
        return "23 14 00 00 00 00 %02X" % n
    return "23 24 00 00 00 00 %02X %02X" % (n >> 8, n & 0xFF)


def memory_answer(size):
    return [0x63] + [i & 0xFF for i in range(size - 1)]


def write_request(total):
    """A WriteMemoryByAddress of @total bytes whose data is the memory's."""
    n = total - 7
    return [0x3D, 0x14, 0, 0, 0, 0, n] + [i & 0xFF for i in range(n)]


def cli_drops(base):
    st, b = get_json(base, "/api/bridges")
    for br in (b or {}).get("bridges", []):
        if "cli" in (br.get("a"), br.get("b")):
            lo = br.get("losses", {})
            return sum(v for side in lo.values() for v in side.values())
    return None


def submit_and_wait(base, window=150):
    st, body = http(base, "POST", "/api/settings/submit", {})
    print("  submit -> %s %s" % (st, body.strip()[:80]))
    if not (st == 200 and '"reboot":true' in body.replace(" ", "")):
        return st == 200
    t0 = time.time()
    seen_down = False
    while time.time() - t0 < window:
        time.sleep(2)
        st, _ = http(base, "GET", "/api/info", timeout=5)
        if st != 200:
            seen_down = True
        elif seen_down:
            print("  back after %.0f s" % (time.time() - t0))
            time.sleep(15)      # the chip provisions ~15 s after a boot
            return True
    print("  the unit did not come back within %d s" % window)
    return False


def put_settings(base, group, saved, changes):
    new = {k: v for k, v in saved.items()
           if k not in ("degraded", "pending_reboot")}
    new.update(changes)
    return http(base, "PUT", "/api/settings/" + group, new)


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


def polls_ok(base):
    st, ap = get_json(base, "/api/autopid")
    return ((ap or {}).get("stats") or {}).get("polls_ok", 0), ap


def leg_answer_whole(c, base, dut, sizes, label):
    drops0 = cli_drops(base)
    for size in sizes:
        want = memory_answer(size)
        r = route(base, memory_request(size))
        got_route = route_bytes(r)
        # one word: this leg is about the answer, L2 about the request's form
        text = console(dut, "uds -t %s -r %s %s" % (
            TX, RX, memory_request(size).replace(" ", "")))
        got_cli, tail = answer_line(text)
        backend = r.get("backend", "?")
        ok = (r.get("ok") is True and got_route == want and got_cli == want
              and re.fullmatch(r"\[%s, \d+ ms\]" % backend, tail) is not None
              and "positive (SID 0x63)" in text)
        c.add("%s: an answer of %d bytes is whole on the route and on the "
              "console" % (label, size), ok,
              "route %s %d B, console %s B, line ends %r" % (
                  "ok" if r.get("ok") else r.get("error"), len(got_route),
                  "no line" if got_cli is None else len(got_cli),
                  tail or text.strip()[-60:]))
    drops1 = cli_drops(base)
    c.add("%s: the console's output lost nothing" % label,
          drops0 is not None and drops1 == drops0,
          "bridge losses %s -> %s" % (drops0, drops1))


def leg_request_as_typed(c, base, dut):
    def run(words):
        return console(dut, "uds -t %s -r %s %s" % (TX, RX, words))

    want = memory_answer(201)
    forms = [
        ("7 bytes, each a word", "23 14 00 00 00 00 C8"),
        ("7 bytes, 0x words", "0x23 0x14 0x00 0x00 0x00 0x00 0xC8"),
        ("7 bytes, one : word", "23:14:00:00:00:00:C8"),
        ("7 bytes, one word", "231400000000C8"),
    ]
    for name, words in forms:
        got, tail = answer_line(run(words))
        c.add("the request as typed (%s) reaches the ECU whole" % name,
              got == want, "console %s B %s" % (
                  "no line" if got is None else len(got),
                  "" if got == want else str(got)[:60]))

    w64 = write_request(64)
    text = run(" ".join("%02X" % b for b in w64))
    got, tail = answer_line(text)
    c.add("a request of 64 bytes, each a word, arrives to the last byte "
          "(the ECU checks every one)", got == [0x7D] + w64[1:7],
          "console %s" % (text.strip()[:70]))

    def refused(name, words):
        st, u0 = get_json(base, "/api/uds")
        age0 = ((u0 or {}).get("last") or {}).get("age_ms", -1)
        time.sleep(0.3)
        text = run(words)
        st, u1 = get_json(base, "/api/uds")
        age1 = ((u1 or {}).get("last") or {}).get("age_ms", -1)
        got, tail = answer_line(text)
        c.add("a request over 64 bytes (%s) is refused and nothing goes to "
              "the ECU" % name,
              got is None and "uds error" not in text and age1 > age0 >= 0,
              "console %r, last transaction %s -> %s ms old" % (
                  text.strip()[:60], age0, age1))

    w65 = write_request(65)
    refused("65 words", " ".join("%02X" % b for b in w65))
    refused("one word of 65 bytes", "".join("%02X" % b for b in w65))
    refused("one : word of 70 bytes",
            ":".join(["3E"] + ["00"] * 69))


def leg_chip(c, base, dut, state):
    st, uds_set = get_json(base, "/api/settings/uds_manager")
    st, ap_set = get_json(base, "/api/settings/autopid")
    state["uds_settings"] = uds_set
    state["autopid_settings"] = ap_set
    staged = False
    if (uds_set or {}).get("backend") != "obd_chip":
        st, body = put_settings(base, "uds_manager", uds_set,
                                {"backend": "obd_chip"})
        c.add("the UDS transport set to the OBD chip", st == 200,
              "%s %s" % (st, body.strip()[:80]))
        state["uds_changed"] = True
        staged = True
    want = {"enabled": True, "custom_enabled": True}
    if any((ap_set or {}).get(k) != v for k, v in want.items()):
        st, body = put_settings(base, "autopid", ap_set, want)
        c.add("Automate switched on", st == 200,
              "%s %s" % (st, body.strip()[:80]))
        state["autopid_changed"] = True
        staged = True
    if staged:
        c.add("the unit restarted and is back", submit_and_wait(base))
    st, u = get_json(base, "/api/uds")
    if not c.add("the live UDS path is the OBD chip",
                 (u or {}).get("backend_active") == "obd_chip",
                 str((u or {}).get("backend_active"))):
        return

    table = {"groups": [{"name": "udsbench", "enabled": True,
                         "period_ms": 250}],
             "pids": [{"name": ROW, "type": "custom", "cmd": "0105",
                       "init": ROW_INIT, "group": "udsbench",
                       "parameters": [{"name": ROW, "expression": "B3-40",
                                       "unit": "degC"}]}],
             "filters": []}
    st, body = http(base, "PUT", "/api/autopid/config", table)
    state["table_changed"] = True
    c.add("the row with a flow control of its own is in the table",
          st == 200, "%s %s" % (st, body.strip()[:80]))

    def row_value(ap):
        for p in (ap or {}).get("params", []):
            if p.get("name") == ROW:
                return p.get("value"), p.get("ts_us")
        return None, None

    deadline = time.time() + 40
    val = ts = None
    while time.time() < deadline and ts is None:
        time.sleep(1)
        _, ap = polls_ok(base)
        val, ts = row_value(ap)
    if not c.add("the row is polled (the simulator's coolant, 90)",
                 ts is not None and val == 90, "value %s" % val):
        return

    # exclusive off: the poller writes to the chip between two requests
    st, body = http(base, "POST", "/api/uds", {"exclusive": False})
    state["exclusive_changed"] = True
    time.sleep(1.0)
    good = after_poll = 0
    fails = []
    for i in range(20):
        last_polls, _ = polls_ok(base)
        end = time.time() + 6
        while time.time() < end:    # the row polled again: its setup is on
            time.sleep(0.15)        # the chip when the request goes out
            now, _ = polls_ok(base)
            if now > last_polls:
                after_poll += 1
                break
        r = route(base, "22 F1 90")
        got = route_bytes(r)
        if r.get("ok") is True and len(got) == 20 and got[:3] == [0x62, 0xF1,
                                                                  0x90]:
            good += 1
        else:
            fails.append("#%d %s" % (i + 1, r.get("error") or
                                     "%d B" % len(got)))
    c.add("the poller wrote to the chip in front of every request (the leg "
          "tests what it names)", after_poll == 20,
          "%d of 20 requests followed a poll" % after_poll)
    c.add("20 multi-frame reads behind the row's flow control, each whole "
          "(the VIN, 20 bytes)", good == 20,
          "%d of 20%s" % (good, (": " + "; ".join(fails[:4])) if fails
                          else ""))

    # exclusive on: the first request of a conversation finds the row's setup
    http(base, "POST", "/api/uds", {"exclusive": True})
    for size in CHIP_SIZES:
        end = time.time() + 25
        p0, _ = polls_ok(base)
        while time.time() < end:    # the hold released and the row polled
            st, u = get_json(base, "/api/uds")
            p1, _ = polls_ok(base)
            if not (u or {}).get("holding") and p1 > p0:
                break
            time.sleep(0.5)
        r = route(base, memory_request(size))
        got = route_bytes(r)
        c.add("OBD chip, a new conversation: an answer of %d bytes is whole"
              % size, r.get("ok") is True and got == memory_answer(size),
              "%s %d B, %s ms" % ("ok" if r.get("ok") else r.get("error"),
                                  len(got), r.get("elapsed_ms")))
    leg_answer_whole(c, base, dut, [201, 4095], "OBD chip")

    http(base, "POST", "/api/uds", {"exclusive": False})
    time.sleep(3.0)
    _, ap = polls_ok(base)
    v0, t0 = row_value(ap)
    time.sleep(2.0)
    _, ap = polls_ok(base)
    v1, t1 = row_value(ap)
    c.add("the row keeps its value behind the UDS requests",
          v1 == 90 and t1 is not None and t1 != t0,
          "value %s, read again: %s" % (v1, t1 != t0))


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081").rstrip("/")
    base = "http://" + dut
    skip_chip = "--skip-chip" in args
    print("UDS CONSOLE bench: dut=%s%s" % (dut, " (no chip leg)" if skip_chip
                                           else ""), flush=True)
    c = Checks()
    state = {}
    saved_cfg = None
    uds0 = None

    try:
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("the unit answers", st == 200 and info,
                     "%s %s" % (st, (info or {}).get("fw_build"))):
            raise SystemExit(1)
        st, uds0 = get_json(base, "/api/uds")
        c.add("the UDS terminal is up", st == 200 and uds0 is not None,
              "path %s, exclusive %s" % ((uds0 or {}).get("backend_active"),
                                         (uds0 or {}).get("exclusive")))
        st, ap = get_json(base, "/api/autopid")
        stats = (ap or {}).get("stats", {})
        c.add("no app holds the chip", not stats.get("paused_client"),
              "paused_client %s" % stats.get("paused_client"))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("the tables saved", st == 200 and saved_cfg is not None)
        text = console(dut, "uds")
        c.add("the console answers on /ws/cli", "usage: uds" in text,
              repr(text.strip()[:60]))
        r = route(base, memory_request(20))
        c.add("the ECU on %s serves its memory" % TX,
              r.get("ok") is True and route_bytes(r) == memory_answer(20),
              "%s %s" % (r.get("backend"), r.get("error") or
                         r.get("response", "")[:30]))
        if c.failed():
            raise SystemExit(1)

        print("L1 the answer whole (path %s)" % uds0.get("backend_active"))
        leg_answer_whole(c, base, dut, L1_SIZES, uds0.get("backend_active"))

        print("L2 the request as typed")
        leg_request_as_typed(c, base, dut)

        if not skip_chip:
            print("L3 the OBD chip behind a row that moved the flow control")
            leg_chip(c, base, dut, state)
    except SystemExit:
        pass
    finally:
        print("L4 restore")
        if state.get("table_changed") and saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            c.add("the tables restored", st == 200,
                  "%s %s" % (st, body.strip()[:80]))
        if state.get("exclusive_changed") and uds0 is not None:
            st, body = http(base, "POST", "/api/uds",
                            {"exclusive": bool(uds0.get("exclusive"))})
            c.add("the exclusive switch restored", st == 200,
                  str(uds0.get("exclusive")))
        staged = False
        if state.get("uds_changed"):
            st, body = put_settings(base, "uds_manager",
                                    state["uds_settings"], {})
            c.add("the UDS settings restored", st == 200,
                  "%s %s" % (st, body.strip()[:80]))
            staged = True
        if state.get("autopid_changed"):
            st, body = put_settings(base, "autopid",
                                    state["autopid_settings"], {})
            c.add("the Automate settings restored", st == 200,
                  "%s %s" % (st, body.strip()[:80]))
            staged = True
        if staged:
            c.add("the unit restarted and is back (restore)",
                  submit_and_wait(base))

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("UDS CONSOLE FAIL: " + "; ".join(failed))
        return 1
    print("UDS CONSOLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
