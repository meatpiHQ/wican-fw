#!/usr/bin/env python3
"""App on the OBD chip bench  ->  APP CHIP PASS / FAIL   (GitHub issue #1078)

An app on a bridge (TCP 35000, the web terminal) and AutoPID share the one
OBD chip. The poller yields while the app drives the chip and takes it back
ten seconds after the app's last byte. Five things were wrong with the way
the chip changed hands (a 2012 Chevrolet Volt, 6.0.0-beta.14, and this bench
on 2026-11-25):

  first     An app's first command landed in the request the poller had on
            the wire: the chip answered `STOPPED` and ran nothing, the app's
            own `ATZ` included (25 of 25 on the bench). Now the app's bytes
            wait until the poller's row has ended.
  reset     With its reset lost, the app went on with what the poller's last
            row had left on the chip (the header, the receive filter, the
            protocol): a `0100` was answered by the gateway the row had
            addressed. A reset that reaches the chip cleans it.
  hold      A monitor (`ATMA`) writes nothing while it runs, so the poller
            took the chip back ten seconds in (`STOPPED`, then the poller's
            answers on the app's stream). meatpi-components pull request #14
            (Jeff Munk): the monitor holds the chip until the app's next
            byte, the chip's prompt, or two minutes; can.send through the
            chip and the test route answer 409 meanwhile.
  gone      An app that went away in the middle of its monitor kept the
            poller off for the two minutes. Now the hold ends when the
            connection that wrote last has no client left.
  quiet     An app that stayed connected and silent received the poller's
            answers. Now what the chip says to the firmware's own requests
            is not handed to the apps.

The bench stores a table of its own (rows that set a header and a receive
filter, asked back to back, so the chip is never idle) and plays the app on
TCP 35000 and on /ws/obd. Traffic for the monitors comes from the unit's own
CAN controller (`POST /api/can/send`, "bus": "can"): six frames with a marker
each, so a monitor's text says which of them the chip showed.

Legs: L0 preflight (the tables and the settings saved, the bench table in,
the poller polling), L1 first (an app's first command is answered, the
poller loses no poll to it), L2 reset (ATZ and ATD as first command: the
banner, every marked frame in the monitor, the car's protocol, 0100 from
7E8), L3 hold (25 s of ATMA with no STOPPED, the poller paused, 409 from
can.send through the chip and from the test route, the stop byte, the poller
back about 10 s later), L4 gone (the app closes in the middle of its
monitor, on TCP and on /ws/obd: the poller back well before the two
minutes), L5 quiet (a connected, silent app receives nothing while the
poller polls; its next command is answered), L6 the cap (ATMA left alone:
STOPPED at two minutes, on the app's stream), L7 restore, no E line.

usage: app_chip_bench.py [--dut localhost:18081] [--elm localhost:18350]
                         [--skip-cap] [--rounds 6] [--out DIR]
  The DUT's web server and its TCP 35000 through an ssh tunnel
  (`ssh -N -L 18081:<dut>:80 -L 18350:<dut>:35000 rpi001`) or by address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7E0 and 7E2, the unit's CAN controller on the bus, the PSU above
the sleep voltage. ~6 min (~3.5 with --skip-cap), no restart (two when
autopid was off at the start).
"""
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import wsmin  # noqa: E402  (the /ws/obd bridge)

YIELD_S = 10.0        # AP_CLIENT_YIELD_MS
CAP_S = 120.0         # OBD_CLIENT_MONITOR_HOLD_MS

# the marked frames of a burst: (id, 29-bit). 7E8 and 7EA are the receive
# filters the bench's rows set.
FRAMES = [("7E8", False), ("7EA", False), ("321", False), ("123", False),
          ("18DAF110", True), ("1ABCDE01", True)]

# the bench's rows: every one sets a header and a receive filter, so the chip
# is never where a reset leaves it. The simulator answers 7E0 on 7E8 and 7E2 on
# 7EA. A row takes about 36 ms with its init, and the group's period of 100 ms
# is shorter than a round of eight, so the rows follow each other without a
# pause: the chip is busy all the time.
ROWS = [("AppA%d" % i, "ATSH7E0;ATFCSH7E0;ATFCSD300000;ATFCSM1;ATCRA7E8;", cmd, expr)
        for i, (cmd, expr) in enumerate([("0104", "B3*100/255"), ("0105", "B3-40"),
                                         ("010C", "[B3:B4]*0.25"), ("010D", "B3")])] + \
       [("AppB%d" % i, "ATSH7E2;ATFCSH7E2;ATFCSD300000;ATFCSM1;ATCRA7EA;", cmd, expr)
        for i, (cmd, expr) in enumerate([("0104", "B3*100/255"), ("0105", "B3-40"),
                                         ("010C", "[B3:B4]*0.25"), ("010D", "B3")])]


def http(base, method, path, body=None, timeout=30):
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


def get_json(base, path, timeout=30):
    st, body = http(base, "GET", path, timeout=timeout)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def table():
    return {"groups": [{"name": "default", "period_ms": 100}],
            "pids": [{"name": name, "type": "custom", "cmd": cmd, "init": init,
                      "group": "default",
                      "parameters": [{"name": name, "expression": expr}]}
                     for name, init, cmd, expr in ROWS],
            "filters": []}


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
            time.sleep(8)
            return True
    print("  the DUT did not come back within %d s" % window)
    return False


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


def client_counters(base):
    """The driver's counters of an app beside the poller (GET /api/obd_chip
    "client"); {} on a firmware without them."""
    st, chip = get_json(base, "/api/obd_chip", timeout=6)
    return (chip or {}).get("client") or {}


def stats(base):
    """(paused_client, polls_ok, polls_failed), None each when not read."""
    st, ap = get_json(base, "/api/autopid", timeout=6)
    s = (ap or {}).get("stats") or {}
    return s.get("paused_client"), s.get("polls_ok"), s.get("polls_failed")


def wait_polling(base, extra=0.0, limit=40.0):
    """Until the poller polls again (not paused for an app, polls_ok
    growing), then `extra` seconds more. False when it did not in `limit`."""
    t0 = time.time()
    last = None
    while time.time() - t0 < limit:
        paused, ok, _ = stats(base)
        if paused is False and last is not None and ok is not None and ok > last:
            time.sleep(extra)
            return True
        last = ok
        time.sleep(0.5)
    return False


def wait_paused(base, want, limit):
    """Seconds until stats.paused_client reads `want`, None when it did not
    in `limit`."""
    t0 = time.time()
    while time.time() - t0 < limit:
        paused, _, _ = stats(base)
        if paused is want:
            return time.time() - t0
        time.sleep(0.25)
    return None


def burst(base):
    """The marked frames once, from the unit's own CAN controller."""
    frames = [{"id": cid, "ext": ext, "data": "03 DE AD %02X 00 00 00 00" % n,
               "gap_ms": 10} for n, (cid, ext) in enumerate(FRAMES, start=1)]
    st, body = http(base, "POST", "/api/can/send", {"bus": "can", "frames": frames},
                    timeout=10)
    try:
        rep = json.loads(body)
    except ValueError:
        rep = {}
    return st == 200 and rep.get("sent") == len(FRAMES)


def seen(text):
    """Which marked frames a monitor's text shows: {id: count}."""
    out = {}
    for line in text.replace(">", "\r").split("\r"):
        flat = line.replace(" ", "").replace("<DATAERROR", "").strip().upper()
        for n, (cid, _) in enumerate(FRAMES, start=1):
            if flat.startswith(cid) and ("DEAD%02X" % n) in flat:
                out[cid] = out.get(cid, 0) + 1
    return out


BANNER = r"ELM327 v\d+\.\d+"   # the chip's ATI, and its ATZ (sent as ATWS)


def reply_of(cmd, ans):
    """What the chip said to `cmd`: the text before the first prompt, without
    the echo of the command (an ATZ goes to the chip as ATWS)."""
    parts = [p.strip() for p in ans.split(">")[0].replace("\n", "\r").split("\r")]
    parts = [p for p in parts if p]
    if parts and parts[0].upper() in (cmd.upper(), "ATWS"):
        parts = parts[1:]
    return " ".join(parts)


def show(text, limit=110):
    t = text.replace("\r", "\\r").replace("\n", "\\n")
    return t if len(t) <= limit else t[:limit] + "..."


class App:
    """One app's connection to the chip: TCP 35000 or the web terminal's
    WebSocket, behind one face."""

    def __init__(self, kind, elm, dut):
        self.kind = kind
        if kind == "tcp":
            host, _, port = elm.partition(":")
            self.s = socket.create_connection((host, int(port or 35000)), timeout=5)
            self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        else:
            host, _, port = dut.partition(":")
            self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=3)
            self.ws.settimeout(0.05)

    def send(self, data):
        if self.kind == "tcp":
            self.s.sendall(data)
        else:
            self.ws.send(data, wsmin.OP_TEXT)

    def _recv(self, wait):
        if self.kind == "tcp":
            self.s.settimeout(wait)
            try:
                return self.s.recv(65536)
            except socket.timeout:
                return b""
            except OSError:
                return b""
        buf = b""
        for _, payload in self.ws.pump():
            buf += payload
        if not buf:
            time.sleep(wait)
        return buf

    def read(self, secs):
        """Everything that arrives in `secs`."""
        buf = b""
        t0 = time.time()
        while time.time() - t0 < secs:
            buf += self._recv(min(0.1, max(0.01, secs - (time.time() - t0))))
        return buf.decode("ascii", "replace")

    def cmd(self, text, timeout=5.0):
        """One command: up to a prompt and then 0.25 s of silence."""
        self.send(text.encode() + b"\r")
        buf = b""
        t0 = time.time()
        quiet_from = None
        while time.time() - t0 < timeout:
            d = self._recv(0.05)
            if d:
                buf += d
                quiet_from = time.time() if b">" in buf else None
            elif quiet_from is not None and time.time() - quiet_from > 0.25:
                break
        return buf.decode("ascii", "replace")

    def close(self):
        try:
            if self.kind == "tcp":
                self.s.close()
            else:
                self.ws.close()
        except OSError:
            pass


class Bench:
    def __init__(self, base, elm, dut, c, log):
        self.base, self.elm, self.dut, self.c, self.log = base, elm, dut, c, log
        self.firsts = []     # (leg, the first command's answer was the chip's)

    def note(self, **kw):
        self.log.write(json.dumps(dict(kw, t=round(time.time(), 2))) + "\n")
        self.log.flush()

    def open(self, leg, kind="tcp", first="ATI", want=BANNER):
        """A new app whose first command is `first`; returns (app, answer,
        answered as the chip answers it). The answer must be the chip's reply
        to this command and nothing else: one prompt, and before it the reply
        alone (`want`, a regular expression), so a word of the poller's on
        the app's stream is not taken for it."""
        app = App(kind, self.elm, self.dut)
        ans = app.cmd(first)
        ok = ans.count(">") == 1 and \
            re.fullmatch(want, reply_of(first, ans)) is not None
        self.firsts.append((leg, ok))
        self.note(leg=leg, kind=kind, first=first, answer=ans, ok=ok)
        return app, ans, ok

    def monitor(self, app, secs, bursts_at):
        """ATMA for `secs` with a burst of traffic at each of `bursts_at`;
        returns the stream's text."""
        app.send(b"ATMA\r")
        t0 = time.time()
        text = ""
        todo = list(bursts_at)
        while time.time() - t0 < secs:
            if todo and time.time() - t0 >= todo[0]:
                todo.pop(0)
                burst(self.base)
            text += app.read(0.2)
        return text

    @staticmethod
    def stop(app):
        app.send(b" ")
        return app.read(1.0)


def leg_first(b, rounds):
    print("L1 first: an app's first command while the poller polls")
    c, base = b.c, b.base
    _, _, failed0 = stats(base)
    cl0 = client_counters(base)
    lost = []
    for r in range(1, rounds + 1):
        if not wait_polling(base, extra=random.uniform(0.0, 0.4)):
            c.add("first %d: the poller polls before the app comes" % r, False)
            continue
        app, ans, ok = b.open("first")
        app.close()
        if not ok:
            lost.append("%d: %s" % (r, show(ans, 40)))
    c.add("the first command of %d apps was answered by the chip (ATI)" % rounds,
          not lost, "; ".join(lost) if lost else "%d of %d" % (rounds, rounds))
    wait_polling(base)
    _, _, failed1 = stats(base)
    c.add("the poller lost no poll to an app's first command",
          failed0 is not None and failed1 == failed0,
          "polls_failed %s -> %s" % (failed0, failed1))
    cl1 = client_counters(base)
    c.add("the driver counted the commands that waited for the poller's pass, "
          "and gave up none",
          cl1.get("turn_waits", 0) > cl0.get("turn_waits", 0) and
          cl1.get("turn_forced") == cl0.get("turn_forced"),
          "turn_waits %s -> %s, the longest %s ms, turn_forced %s -> %s" % (
              cl0.get("turn_waits"), cl1.get("turn_waits"), cl1.get("turn_wait_max_ms"),
              cl0.get("turn_forced"), cl1.get("turn_forced")))


def leg_reset(b):
    print("L2 reset: the reset as an app's first command cleans the chip")
    c, base = b.c, b.base
    for word in ("ATZ", "ATD", "ATZ"):
        if not wait_polling(base, extra=random.uniform(0.0, 0.4)):
            c.add("reset %s: the poller polls before the app comes" % word, False)
            continue
        app, ans, ok = b.open("reset", first=word,
                              want=BANNER if word == "ATZ" else "OK")
        c.add("%s as the first command is answered by the chip" % word, ok,
              show(ans, 60))
        app.cmd("ATE0")
        app.cmd("ATH1")
        text = b.monitor(app, 4.0, [1.0, 2.0, 3.0])
        b.stop(app)
        ids = seen(text)
        c.add("%s: the monitor behind it shows every marked frame" % word,
              all(ids.get(cid, 0) == 3 for cid, _ in FRAMES),
              json.dumps(ids, sort_keys=True) if ids else "none shown")
        dpn = app.cmd("ATDPN")
        c.add("%s: the chip is on the car's protocol" % word,
              dpn.split(">")[0].strip().lstrip("A") == "6", show(dpn, 30))
        obd = app.cmd("0100", timeout=6.0)
        flat = obd.replace(" ", "")
        c.add("%s: 0100 behind it is answered by 7E8 alone" % word,
              "7E8064100" in flat and "7EA" not in flat, show(obd, 60))
        app.close()
        b.note(leg="reset", word=word, ids=ids, dpn=dpn, obd=obd)


def leg_hold(b):
    print("L3 hold: an app's monitor holds the chip")
    c, base = b.c, b.base
    if not wait_polling(base):
        c.add("hold: the poller polls before the app comes", False)
        return
    app, _, _ = b.open("hold")
    app.cmd("ATZ")
    app.cmd("ATE0")
    app.cmd("ATH1")
    app.send(b"ATMA\r")
    t0 = time.time()
    text = ""
    paused, idle, sends = [], [], []
    send_obd = test_req = None
    next_burst = 2.0
    while time.time() - t0 < 25.0:
        now = time.time() - t0
        if now >= next_burst:
            sends.append(burst(base))
            next_burst += 4.0
            p, _, _ = stats(base)
            paused.append(p)
            st, chip = get_json(base, "/api/obd_chip", timeout=6)
            idle.append((chip or {}).get("client_idle_ms"))
        if send_obd is None and now >= 13.0:
            send_obd = http(base, "POST", "/api/can/send",
                            {"bus": "obd", "id": "7DF", "data": "02 01 0C"}, timeout=10)
        if test_req is None and now >= 16.0:
            test_req = http(base, "POST", "/api/autopid/test",
                            {"cmd": "010C", "type": "custom"}, timeout=10)
        text += app.read(0.2)
    ids = seen(text)
    c.add("25 s of ATMA: no STOPPED on the app's stream", "STOPPED" not in text,
          "STOPPED after %d bytes" % text.find("STOPPED") if "STOPPED" in text
          else "%d bytes" % len(text))
    c.add("the monitor showed the marked frames to the end",
          len(sends) >= 5 and all(ids.get(cid, 0) == len(sends) for cid, _ in FRAMES),
          "%d bursts, %s" % (len(sends), json.dumps(ids, sort_keys=True)))
    c.add("the poller stayed off (stats.paused_client)",
          bool(paused) and all(p is True for p in paused), "%r" % paused)
    c.add("client_idle_ms reads 0 while the monitor runs",
          bool(idle) and all(i == 0 for i in idle), "%r" % idle)
    c.add("can.send through the chip answers 409 meanwhile",
          send_obd is not None and send_obd[0] == 409,
          "%s %s" % (send_obd[0], send_obd[1].strip()[:110]) if send_obd else "not asked")
    c.add("the test route answers 409 meanwhile",
          test_req is not None and test_req[0] == 409,
          "%s %s" % (test_req[0], test_req[1].strip()[:110]) if test_req else "not asked")
    t_stop = time.time()
    tail = b.stop(app)
    c.add("the app's next byte stops the monitor at once", "STOPPED" in tail and ">" in tail,
          show(tail, 40))
    back = wait_paused(base, False, 20.0)
    since = (time.time() - t_stop) if back is not None else None
    c.add("the poller is back about 10 s after the app's last byte",
          since is not None and YIELD_S - 1.5 <= since <= YIELD_S + 4.0,
          "%.1f s" % since if since is not None else "not in 20 s")
    app.close()
    b.note(leg="hold", ids=ids, paused=paused, idle=idle, back_s=since)


def leg_gone(b, kind):
    print("L4 gone (%s): the app closes in the middle of its monitor" % kind)
    c, base = b.c, b.base
    if not wait_polling(base):
        c.add("gone %s: the poller polls before the app comes" % kind, False)
        return
    app, _, _ = b.open("gone", kind=kind)
    app.cmd("ATZ")
    app.cmd("ATE0")
    app.cmd("ATH1")
    t0 = time.time()
    text = b.monitor(app, 5.0, [2.0])
    ids = seen(text)
    c.add("%s: the monitor ran (marked frames shown, no STOPPED)" % kind,
          len(ids) == len(FRAMES) and "STOPPED" not in text,
          json.dumps(ids, sort_keys=True))
    gone0 = client_counters(base).get("gone")
    app.close()
    t_close = time.time()
    back = wait_paused(base, False, 30.0)
    total = (time.time() - t0) if back is not None else None
    c.add("%s: the poller is back when the app has gone, not at the two minutes" % kind,
          total is not None and total <= YIELD_S + 6.0,
          "%.1f s after ATMA, %.1f s after the close" % (total, time.time() - t_close)
          if total is not None else "not within 30 s of the close")
    gone1 = client_counters(base).get("gone")
    c.add("%s: the driver counted the hold it ended (client.gone)" % kind,
          gone0 is not None and gone1 == gone0 + 1, "%s -> %s" % (gone0, gone1))
    if back is None:
        left = CAP_S + YIELD_S - (time.time() - t0)
        print("  waiting %.0f s for the two minutes to pass" % max(left, 0))
        wait_paused(base, False, max(left, 0) + 20.0)
    ok = wait_polling(base)
    _, _, failed = stats(base)
    c.add("%s: the poller polls again behind it" % kind, ok, "polls_failed %s" % failed)
    b.note(leg="gone", kind=kind, back_s=total)


def leg_quiet(b):
    print("L5 quiet: a connected, silent app gets nothing of the poller's")
    c, base = b.c, b.base
    if not wait_polling(base):
        c.add("quiet: the poller polls before the app comes", False)
        return
    st, chip0 = get_json(base, "/api/obd_chip", timeout=6)
    app, _, _ = b.open("quiet")
    _, ok0, _ = stats(base)
    text = app.read(YIELD_S + 8.0)
    paused, ok1, _ = stats(base)
    c.add("the poller polled again while the app stayed connected",
          paused is False and ok0 is not None and ok1 is not None and ok1 > ok0 + 5,
          "paused_client %s, polls_ok %s -> %s" % (paused, ok0, ok1))
    c.add("the silent app received nothing in %.0f s" % (YIELD_S + 8.0), text == "",
          "%d bytes: %s" % (len(text), show(text, 80)) if text else "0 bytes")
    _, _, failed0 = stats(base)
    ans = app.cmd("ATRV")
    c.add("its next command is answered by the chip, and by nothing else (ATRV)",
          ans.count(">") == 1 and
          re.fullmatch(r"\d+\.\d+V", reply_of("ATRV", ans)) is not None, show(ans, 40))
    st, chip1 = get_json(base, "/api/obd_chip", timeout=6)
    kept0 = sum(s.get("withheld", 0) for s in (chip0 or {}).get("subscribers", []))
    kept1 = sum(s.get("withheld", 0) for s in (chip1 or {}).get("subscribers", []))
    c.add("the driver counted what it kept from the apps (subscribers[].withheld)",
          kept1 > kept0, "%s -> %s" % (kept0, kept1))
    app.close()
    wait_polling(base)
    _, _, failed1 = stats(base)
    c.add("the poller lost no poll to that command", failed1 == failed0,
          "polls_failed %s -> %s" % (failed0, failed1))
    b.note(leg="quiet", bytes=len(text), text=text[:400])


def leg_cap(b):
    print("L6 the cap: a monitor left alone ends at two minutes")
    c, base = b.c, b.base
    if not wait_polling(base):
        c.add("cap: the poller polls before the app comes", False)
        return
    app, _, _ = b.open("cap")
    app.cmd("ATZ")
    app.cmd("ATE0")
    app.cmd("ATH1")
    app.send(b"ATMA\r")
    t0 = time.time()
    text = ""
    t_stopped = None
    paused_mid = None
    while time.time() - t0 < CAP_S + 15.0:
        text += app.read(0.5)
        if paused_mid is None and time.time() - t0 >= 60.0:
            paused_mid, _, _ = stats(base)
        if "STOPPED" in text:
            t_stopped = time.time() - t0
            break
    c.add("the poller is still off a minute into the monitor", paused_mid is True,
          "paused_client %r" % paused_mid)
    c.add("STOPPED on the app's stream at the two minutes",
          t_stopped is not None and CAP_S - 2.0 <= t_stopped <= CAP_S + 6.0,
          "%.1f s" % t_stopped if t_stopped is not None else
          "none in %.0f s" % (CAP_S + 15.0))
    app.close()
    ok = wait_polling(base)
    c.add("the poller polls again behind the cap", ok)
    b.note(leg="cap", stopped_s=t_stopped)


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081").rstrip("/")
    elm = opt("--elm", "localhost:18350")
    rounds = int(opt("--rounds", 6))
    base = "http://" + dut
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs", "app_chip_" + ts))
    os.makedirs(out, exist_ok=True)
    print("APP CHIP bench: dut=%s elm=%s rounds=%d out=%s" % (dut, elm, rounds, out),
          flush=True)
    random.seed(1078)
    c = Checks()
    saved_cfg = saved_settings = None
    settings_changed = False
    status0 = {}
    log = open(os.path.join(out, "legs.jsonl"), "w")
    b = Bench(base, elm, dut, c, log)

    try:
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s %s" % (st, (info or {}).get("fw_build"),
                                   (info or {}).get("partition"))):
            raise SystemExit(1)
        st, status0 = get_json(base, "/api/status")
        status0 = status0 or {}
        c.add("status read", st == 200 and "boot_count" in status0,
              "boot_count %s, unexpected_resets %s" % (
                  status0.get("boot_count"), status0.get("unexpected_resets")))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None)
        st, saved_settings = get_json(base, "/api/settings/autopid")
        c.add("settings saved", st == 200 and saved_settings is not None)
        with open(os.path.join(out, "saved_config.json"), "w") as f:
            json.dump(saved_cfg, f)
        with open(os.path.join(out, "saved_settings.json"), "w") as f:
            json.dump(saved_settings, f)
        st, can = get_json(base, "/api/can")
        c.add("the unit's CAN controller is on the bus (the traffic's source)",
              st == 200 and (can or {}).get("state") == "running" and
              not (can or {}).get("listen_only"),
              "state %s, listen_only %s" % ((can or {}).get("state"),
                                            (can or {}).get("listen_only")))
        if c.failed():
            raise SystemExit(1)
        new_settings = dict(saved_settings)
        for k in ("degraded", "pending_reboot"):
            new_settings.pop(k, None)
        want = {"enabled": True, "custom_enabled": True}
        if any(new_settings.get(k) != v for k, v in want.items()):
            new_settings.update(want)
            st, body = http(base, "PUT", "/api/settings/autopid", new_settings)
            c.add("autopid switched on (settings PUT)", st == 200,
                  "%s %s" % (st, body.strip()[:100]))
            settings_changed = True
            c.add("the DUT rebooted and is back", submit_and_wait(base))
            st, status0 = get_json(base, "/api/status")
            status0 = status0 or {}
        st, body = http(base, "PUT", "/api/autopid/config", table())
        if not c.add("the bench's tables accepted", st == 200,
                     "%s %s" % (st, body.strip()[:100])):
            raise SystemExit(1)
        c.add("the poller polls the bench's rows", wait_polling(base, extra=3.0))
        _, ok_a, failed_a = stats(base)
        time.sleep(3.0)
        _, ok_b, failed_b = stats(base)
        c.add("the rows are answered, back to back (the chip is never idle)",
              ok_a is not None and ok_b - ok_a >= 3 and failed_b == failed_a,
              "polls_ok +%s in 3 s, polls_failed +%s" % (
                  (ok_b - ok_a) if ok_a is not None else "?",
                  (failed_b - failed_a) if failed_a is not None else "?"))
        c.add("a burst of marked frames goes out (can.send, bus can)", burst(base))
        if c.failed():
            raise SystemExit(1)

        leg_first(b, rounds)
        leg_reset(b)
        leg_hold(b)
        leg_gone(b, "tcp")
        leg_gone(b, "ws")
        leg_quiet(b)
        if "--skip-cap" not in args:
            leg_cap(b)
        answered = sum(1 for _, ok in b.firsts if ok)
        c.add("every app's first command in this run was answered by the chip",
              answered == len(b.firsts), "%d of %d" % (answered, len(b.firsts)))
    finally:
        print("L7 restore")
        if saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            c.add("tables restored", st == 200, "%s %s" % (st, body.strip()[:80]))
        if settings_changed and saved_settings is not None:
            restore = dict(saved_settings)
            for k in ("degraded", "pending_reboot"):
                restore.pop(k, None)
            st, body = http(base, "PUT", "/api/settings/autopid", restore)
            c.add("settings restored", st == 200, "%s %s" % (st, body.strip()[:80]))
            c.add("the DUT rebooted and is back (restore)", submit_and_wait(base))
        c.add("the poller polls the restored tables", wait_polling(base, limit=150.0))
        st, end = get_json(base, "/api/status")
        end = end or {}
        health = end.get("health") or {}
        c.add("no E line since the boot, no fault, no unexpected restart",
              st == 200 and health.get("log_errors") == 0 and
              health.get("faults") == 0 and
              end.get("unexpected_resets") == status0.get("unexpected_resets"),
              "log_errors %s, faults %s, unexpected_resets %s -> %s" % (
                  health.get("log_errors"), health.get("faults"),
                  status0.get("unexpected_resets"), end.get("unexpected_resets")))
        log.close()

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("APP CHIP FAIL: " + "; ".join(failed))
        return 1
    print("APP CHIP PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
