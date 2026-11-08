#!/usr/bin/env python3
"""App session capture: the device's side of a phone app's session, on record.

A user says "Car Scanner pairs and shows nothing", "RealDash takes four
tries", "the iPhone drops every second over WiFi". The device side of such
a session is in three places at once: the console (the Bluetooth session
lines of 2026-11-08, the socket server's client lines, the interface
manager's handover lines, every W and E line), the counters (`/api/ble`,
`/api/bridges`, `/api/obd_chip`, `/api/sockets`, `/api/status`) and the
log ring. This tool records all three against one clock while a person
drives the app on a phone, takes their marks ("start carscanner android
ble", "pin typed", "gauge moving"), and writes a summary per leg: the BLE
sessions with their milestones, the socket clients, the bytes that flowed
on each bridge, the drops, the E and W lines.

  python tools/testbench/ble/app_session_capture.py --session NAME
         [--witness 192.168.82.1] [--port auto|COM254|none]
         [--interval 1] [--secs N] [--no-stdin] [--out DIR]

Session folder (default test-reports/logs/app_sessions/<date>_<NAME>/):
  console.log     the console, every line, wall-clock stamped (console_tail)
  timeline.log    the merged story: console lines of the watched tags,
                  counter changes, marks (what you read live)
  counters.jsonl  one snapshot of the counters per poll
  ring_end.txt    the device's log ring at the end
  summary.md      per leg: the BLE sessions, the sockets, the deltas

While it runs, type on its terminal (Enter sends), or append the same
lines to the marks file (`--marks-file`, default `<folder>/marks_in.txt`:
the way a helper on another terminal, or Claude from the chat, drives a
run that has no terminal of its own):
  start <leg name>   a new leg (an app on a platform on a transport)
  end                closes the leg and writes its summary
  <anything else>    a mark with the wall clock, into the timeline
  ring               the log ring now, into the timeline
  q                  stop (the open leg is closed first)

`--no-stdin --secs N` is the unattended form (one leg named after the
session, the dry runs); `--no-stdin --secs 0` runs until a `q` line
arrives in the marks file and opens no leg by itself.

The witness address: the device's USB link in the device role
(192.168.82.1, `app_session_profile.py ble-on` sets it) stays up while a
Bluetooth client holds the link and the station is suspended (the
sta_ble_handover rule), which the bench AP address does not. `--witness
none` runs on the console alone.

The console port: opening it MAY reset the device once at the start
(bench_rig.md); the capture notes the boot it sees. `--port none` skips it.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
LIB = os.path.join(REPO, "tools", "testbench", "lib")
sys.path.insert(0, LIB)

ROUTES = ("/api/status", "/api/ble", "/api/bridges", "/api/obd_chip", "/api/sockets")

# console lines worth the timeline (the rest stays in console.log)
TAGS = ("ble_manager", "interface_manager", "bridge_manager", "socket_manager",
        "obd_chip", "autopid", "wifi_manager", "websocket_manager", "NimBLE",
        "restart_tracker", "usb_host_manager", "can_manager", "translator")
TAG_RE = re.compile(r"^[IWE] \(\d+\) (" + "|".join(re.escape(t) for t in TAGS) + r"):")
LEVEL_RE = re.compile(r"^([WE]) \(\d+\) ")
WIFI_STATE_RE = re.compile(r"wifi:state:|wifi:connected with|wifi:disconnect|STA got IP|BOOT|"
                           r"WICAN (MEM|RAMMAP|CAPS|FLASH)|cpu_start|rst:0x", re.I)
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")
# echo the timeline to stdout only for a person at a terminal; a redirected
# stdout gets nothing (the files are the record) so it can never block
ECHO = bool(sys.stdout and sys.stdout.isatty())
# NimBLE prints two INFO lines per notification (console spam, README): not the timeline
NOISE_RE = re.compile(r"GATT procedure initiated: notify|att_handle=|GAP procedure initiated: $")

# the Bluetooth session lines (ble_manager README, "Session log lines")
BLE_LINE = re.compile(r"ble_manager: (client connected|connection update|security request sent|"
                      r"pairing success|pairing failed|data pipe|first app write|"
                      r"client disconnected)(.*)$")


def stamp(t=None):
    t = time.time() if t is None else t
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


class Timeline:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")
        self.lock = threading.Lock()
        self.lines = []          # (t, src, text) for the summaries

    def put(self, src, text, t=None, echo=True):
        t = time.time() if t is None else t
        line = f"{stamp(t)} {src:<4} {text}"
        with self.lock:
            self.f.write(line + "\n")
            self.f.flush()
            self.lines.append((t, src, text))
        # the echo stays outside the lock and never stops the run: a stdout
        # redirected into a pipe nobody drains blocks print() for good
        # (2026-11-08: the day's recorder froze at 12:03 that way), and a
        # console in cp1252 chokes on an odd byte
        if echo and ECHO:
            try:
                print(line, flush=True)
            except (OSError, ValueError, UnicodeError):
                pass

    def close(self):
        self.f.close()


class ConsoleTail:
    """console_tail.py as a child, its file followed for the timeline."""

    def __init__(self, port, log, tl):
        self.port, self.log, self.tl = port, log, tl
        self.stop_file = log + ".stop"
        self.proc = None
        self.alive = False

    def start(self):
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(LIB, "console_tail.py"), self.port,
             self.log, "--stop-file", self.stop_file, "--secs", "86400"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.alive = True
        threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self):
        while not os.path.exists(self.log) and self.alive:
            time.sleep(0.2)
        with open(self.log, "r", encoding="utf-8", errors="replace") as f:
            while self.alive:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                line = line.rstrip("\n")
                # "HH:MM:SS.mmm <text>" from console_tail
                text = line[13:] if len(line) > 13 and line[12] == " " else line
                text = ANSI.sub("", text)
                if NOISE_RE.search(text):
                    continue
                if TAG_RE.match(text) or LEVEL_RE.match(text) or WIFI_STATE_RE.search(text):
                    self.tl.put("CON", text)

    def stop(self):
        self.alive = False
        try:
            open(self.stop_file, "w").close()
            self.proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.remove(self.stop_file)
        except OSError:
            pass


def get(host, path, timeout=2.5, raw=False):
    try:
        with urllib.request.urlopen(f"http://{host}{path}", timeout=timeout) as r:
            blob = r.read()
            return blob.decode(errors="replace") if raw else json.loads(blob)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


class Witness:
    """The counters, polled; changes go to the timeline."""

    WATCH_BITS = ("ble_connected", "sta_connected", "sta_suspended", "ap_suspended",
                  "ble_suspended", "ble_enabled", "autopid_idle", "awake")

    def __init__(self, host, interval, tl, jsonl):
        self.host, self.interval, self.tl = host, interval, tl
        self.f = open(jsonl, "a", encoding="utf-8")
        self.alive = True
        self.last = None            # the last snapshot
        self.reach = None
        self.snaps = []             # (t, snapshot) for the leg deltas

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _snapshot(self):
        s = {"t": time.time()}
        st = get(self.host, "/api/status")
        if st is None:
            return None
        s["bits"] = st.get("bits", {})
        s["uptime"] = st.get("uptime")
        s["boot_count"] = st.get("boot_count")
        s["unexpected_resets"] = st.get("unexpected_resets")
        s["mem_free"] = st.get("memory", {}).get("internal", {}).get("free")
        s["mem_min"] = st.get("memory", {}).get("internal", {}).get("min_free")
        h = st.get("health", {})
        s["log_errors"], s["log_warnings"] = h.get("log_errors"), h.get("log_warnings")
        s["faults"] = h.get("faults")
        b = get(self.host, "/api/ble") or {}
        s["ble"] = {k: b.get(k) for k in ("enabled", "connected", "secured", "phy_tx",
                                          "max_payload")}
        s["ble_ch"] = {c["name"]: {k: c.get(k) for k in ("rx_bytes", "tx_bytes", "rx_overflow",
                                                         "tx_timeouts", "tx_link_down", "out")}
                       for c in b.get("channels", [])}
        br = get(self.host, "/api/bridges") or {}
        s["bridges"] = {x["name"]: dict(x.get("stats", {}), up=x.get("up"),
                                        enabled=x.get("enabled"))
                        for x in br.get("bridges", [])}
        oc = get(self.host, "/api/obd_chip") or {}
        s["chip"] = {"ready": oc.get("ready"), "claim": oc.get("claim"),
                     "idle_ms": oc.get("client_idle_ms"),
                     "rx_overflows": oc.get("uart", {}).get("rx_overflows"),
                     "subs": {f"{x.get('name')}{x.get('idx')}": x.get("dropped")
                              for x in oc.get("subscribers", [])}}
        so = get(self.host, "/api/sockets") or {}
        s["sockets"] = {x["name"]: dict(x.get("stats", {}), bridge=x.get("bridge"))
                        for x in so.get("servers", []) if x.get("enabled")}
        return s

    def _diff(self, a, b):
        """Human lines for what changed between two snapshots."""
        out = []
        if a is None:
            out.append(f"witness up: fw boot #{b['boot_count']} uptime {b['uptime']}, "
                       f"ble enabled={b['ble'].get('enabled')} connected={b['ble'].get('connected')}, "
                       f"bits " + " ".join(k for k in self.WATCH_BITS if b["bits"].get(k)))
            return out
        if b["boot_count"] != a["boot_count"] or (b["uptime"] or "") < (a["uptime"] or ""):
            out.append(f"DEVICE REBOOTED: boot #{a['boot_count']} -> #{b['boot_count']}, "
                       f"unexpected_resets {b['unexpected_resets']}")
        for k in self.WATCH_BITS:
            if a["bits"].get(k) != b["bits"].get(k):
                out.append(f"bit {k}: {a['bits'].get(k)} -> {b['bits'].get(k)}")
        for k in ("connected", "secured", "phy_tx", "max_payload"):
            if a["ble"].get(k) != b["ble"].get(k):
                out.append(f"ble {k}: {a['ble'].get(k)} -> {b['ble'].get(k)}")
        for name, st in b["bridges"].items():
            old = a["bridges"].get(name, {})
            d_ab = (st.get("a2b_bytes") or 0) - (old.get("a2b_bytes") or 0)
            d_ba = (st.get("b2a_bytes") or 0) - (old.get("b2a_bytes") or 0)
            if d_ab or d_ba:
                out.append(f"bridge {name}: +{d_ab} B a->b, +{d_ba} B b->a"
                           + (f", send_errors {st.get('send_errors')}"
                              if st.get("send_errors") != old.get("send_errors") else "")
                           + (f", codec_errors {st.get('codec_errors')}"
                              if st.get("codec_errors") != old.get("codec_errors") else ""))
            elif st.get("up") != old.get("up"):
                out.append(f"bridge {name}: up {old.get('up')} -> {st.get('up')}")
        for name, st in b["sockets"].items():
            old = a["sockets"].get(name, {})
            for k in ("clients", "refused", "reconnects", "rx_drops", "tx_drops"):
                if st.get(k) != old.get(k):
                    out.append(f"socket {name} {k}: {old.get(k)} -> {st.get(k)}")
            d_in = (st.get("bytes_in") or 0) - (old.get("bytes_in") or 0)
            d_out = (st.get("bytes_out") or 0) - (old.get("bytes_out") or 0)
            if d_in or d_out:
                out.append(f"socket {name}: +{d_in} B in, +{d_out} B out")
        for name, st in b["ble_ch"].items():
            old = a["ble_ch"].get(name, {})
            for k in ("rx_overflow", "tx_timeouts", "tx_link_down", "out"):
                if st.get(k) != old.get(k):
                    out.append(f"ble channel {name} {k}: {old.get(k)} -> {st.get(k)}")
        if b["chip"].get("claim") != a["chip"].get("claim"):
            out.append(f"chip claim: {a['chip'].get('claim')} -> {b['chip'].get('claim')}")
        if b["chip"].get("ready") != a["chip"].get("ready"):
            out.append(f"chip ready: {a['chip'].get('ready')} -> {b['chip'].get('ready')}")
        if b["chip"].get("rx_overflows") != a["chip"].get("rx_overflows"):
            out.append(f"chip uart rx_overflows: {a['chip'].get('rx_overflows')} -> "
                       f"{b['chip'].get('rx_overflows')}")
        for name, dropped in b["chip"].get("subs", {}).items():
            if dropped != a["chip"].get("subs", {}).get(name):
                out.append(f"chip subscriber {name} dropped: "
                           f"{a['chip'].get('subs', {}).get(name)} -> {dropped}")
        for k in ("log_errors", "faults"):
            if b.get(k) != a.get(k):
                out.append(f"health {k}: {a.get(k)} -> {b.get(k)}")
        return out

    def _run(self):
        down_since = None
        while self.alive:
            t0 = time.time()
            s = self._snapshot()
            if s is None:
                if self.reach is not False:
                    self.reach = False
                    down_since = t0
                    self.tl.put("WIT", f"witness {self.host} unreachable (the station "
                                "suspended, a reboot, or the USB link re-enumerating)")
                self.f.write(json.dumps({"t": t0, "reach": False}) + "\n")
            else:
                if self.reach is not True:
                    if down_since is not None:
                        s_down = round(t0 - down_since)
                        self.tl.put("WIT", f"witness back after {s_down} s")
                    self.reach = True
                for line in self._diff(self.last, s):
                    self.tl.put("WIT", line)
                self.last = s
                self.snaps.append(s)
                self.f.write(json.dumps(s) + "\n")
            self.f.flush()
            time.sleep(max(0.2, self.interval - (time.time() - t0)))

    def stop(self):
        self.alive = False
        self.f.close()


class Leg:
    def __init__(self, name, t0, wit_snap):
        self.name, self.t0, self.t1 = name, t0, None
        self.snap0 = wit_snap


def leg_summary(leg, tl, wit):
    """The markdown section of one leg from the timeline and the snapshots."""
    t0, t1 = leg.t0, leg.t1 or time.time()
    lines = [l for l in tl.lines if t0 <= l[0] <= t1]
    out = [f"## Leg: {leg.name} ({stamp(t0)[:8]} to {stamp(t1)[:8]}, "
           f"{round(t1 - t0)} s)", ""]
    marks = [f"{stamp(t)[:12]} {txt}" for t, src, txt in lines if src == "MARK"]
    if marks:
        out += ["Marks:"] + [f"- {m}" for m in marks] + [""]

    # the Bluetooth sessions, as the console told them
    sessions, cur = [], None
    for t, src, txt in lines:
        if src != "CON":
            continue
        m = BLE_LINE.search(txt)
        if not m:
            continue
        kind, rest = m.group(1), m.group(2).strip(": ")
        if kind == "client connected":
            cur = {"t0": t, "connected": rest, "events": [], "end": None}
            sessions.append(cur)
        elif cur is not None:
            if kind == "client disconnected":
                cur["end"] = (t, rest)
                cur = None
            else:
                cur["events"].append((round(t - cur["t0"], 1), kind, rest))
    if sessions:
        out.append("Bluetooth sessions (the console):")
        for i, s in enumerate(sessions, 1):
            out.append(f"- session {i} at {stamp(s['t0'])[:12]}: connected {s['connected']}")
            for dt, kind, rest in s["events"]:
                out.append(f"  - +{dt} s {kind} {rest}")
            if s["end"]:
                out.append(f"  - {stamp(s['end'][0])[:12]} disconnected: {s['end'][1]}")
            else:
                out.append("  - still connected at the end of the leg")
        out.append("")
    else:
        out += ["Bluetooth sessions (the console): none", ""]

    sock = [f"{stamp(t)[:12]} {txt}" for t, src, txt in lines
            if src == "CON" and "socket_manager:" in txt]
    if sock:
        out += ["Socket server (the console):"] + [f"- {s}" for s in sock] + [""]
    iface = [f"{stamp(t)[:12]} {txt}" for t, src, txt in lines
             if src == "CON" and ("interface_manager:" in txt or "wifi:state" in txt
                                  or "STA got IP" in txt)]
    if iface:
        out += ["Handover and WiFi (the console):"] + [f"- {s}" for s in iface] + [""]

    # the witness deltas over the leg
    snaps = [s for s in wit.snaps if t0 <= s["t"] <= t1] if wit else []
    if len(snaps) >= 2:
        a, b = snaps[0], snaps[-1]
        d = []
        for name, st in b["bridges"].items():
            old = a["bridges"].get(name, {})
            d.append(f"{name}: a->b +{(st.get('a2b_bytes') or 0) - (old.get('a2b_bytes') or 0)} B "
                     f"({(st.get('a2b_chunks') or 0) - (old.get('a2b_chunks') or 0)} chunks), "
                     f"b->a +{(st.get('b2a_bytes') or 0) - (old.get('b2a_bytes') or 0)} B "
                     f"({(st.get('b2a_chunks') or 0) - (old.get('b2a_chunks') or 0)} chunks), "
                     f"send_errors +{(st.get('send_errors') or 0) - (old.get('send_errors') or 0)}")
        out += ["Bridges over the leg (the witness):"] + [f"- {x}" for x in d]
        for name, st in b["sockets"].items():
            old = a["sockets"].get(name, {})
            out.append(f"- socket {name}: in +{(st.get('bytes_in') or 0) - (old.get('bytes_in') or 0)} B, "
                       f"out +{(st.get('bytes_out') or 0) - (old.get('bytes_out') or 0)} B, "
                       f"reconnects +{(st.get('reconnects') or 0) - (old.get('reconnects') or 0)}, "
                       f"refused +{(st.get('refused') or 0) - (old.get('refused') or 0)}, "
                       f"rx_drops +{(st.get('rx_drops') or 0) - (old.get('rx_drops') or 0)}, "
                       f"tx_drops +{(st.get('tx_drops') or 0) - (old.get('tx_drops') or 0)}")
        subs = {k: (v or 0) - (a["chip"].get("subs", {}).get(k) or 0)
                for k, v in b["chip"].get("subs", {}).items()}
        out.append(f"- OBD chip: subscriber drops {subs}, uart rx_overflows "
                   f"+{(b['chip'].get('rx_overflows') or 0) - (a['chip'].get('rx_overflows') or 0)}, "
                   f"ready {b['chip'].get('ready')}")
        for name, st in b["ble_ch"].items():
            old = a["ble_ch"].get(name, {})
            out.append(f"- BLE channel {name}: rx_overflow +{(st.get('rx_overflow') or 0) - (old.get('rx_overflow') or 0)}, "
                       f"tx_timeouts +{(st.get('tx_timeouts') or 0) - (old.get('tx_timeouts') or 0)}, "
                       f"tx_link_down +{(st.get('tx_link_down') or 0) - (old.get('tx_link_down') or 0)}")
        out.append(f"- health: log_errors {a.get('log_errors')} -> {b.get('log_errors')}, "
                   f"log_warnings {a.get('log_warnings')} -> {b.get('log_warnings')}, faults "
                   f"{b.get('faults')}, internal free min {min(s.get('mem_min') or 0 for s in snaps)} B, "
                   f"boot #{a.get('boot_count')} -> #{b.get('boot_count')}")
        unreach = sum(1 for l in lines if l[1] == "WIT" and "unreachable" in l[2])
        if unreach:
            out.append(f"- the witness was unreachable {unreach} time(s) during the leg")
        out.append("")
    elif wit:
        out += ["Bridges over the leg (the witness): fewer than two snapshots "
                "(unreachable: no USB link, or the station suspended)", ""]

    ew = [f"{stamp(t)[:12]} {txt}" for t, src, txt in lines
          if src == "CON" and LEVEL_RE.match(txt)]
    out += [f"W and E lines: {len(ew)}"] + [f"- {x}" for x in ew[:60]] + [""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True)
    ap.add_argument("--witness", default="192.168.82.1",
                    help="the device's HTTP address to poll, or none")
    ap.add_argument("--port", default="auto",
                    help="the console port (auto = detect_ports' wican_console), or none")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--secs", type=float, default=0, help="stop after N s (0 = until q)")
    ap.add_argument("--no-stdin", action="store_true")
    ap.add_argument("--marks-file", default="", help="lines appended here act as typed input")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    # a Windows console in cp1252 must never kill the run over one odd character
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    date = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = a.out or os.path.join(REPO, "test-reports", "logs", "app_sessions",
                                f"{date}_{re.sub(r'[^A-Za-z0-9_.-]', '_', a.session)}")
    os.makedirs(out, exist_ok=True)
    tl = Timeline(os.path.join(out, "timeline.log"))
    tl.put("MARK", f"session '{a.session}' starts; folder {out}")

    port = a.port
    if port == "auto":
        port = "none"
        try:
            from bench_ports import resolve  # tools/testbench/lib
            port = resolve("auto", "wican_console", "none") or "none"
        except Exception as e:  # noqa: BLE001
            tl.put("MARK", f"console port not resolved ({e}); --port COMxx to name it")
    tail = None
    if port != "none":
        # one console file per start: a restart on the same folder must not
        # re-read the old file and then sit past the new one's end (the
        # console tail truncates its file; the follower of 2026-11-08 13:23
        # delivered nothing for 40 minutes that way)
        console_name = f"console_{time.strftime('%H%M%S')}.log"
        tail = ConsoleTail(port, os.path.join(out, console_name), tl)
        tail.start()
        tl.put("MARK", f"console tail on {port} (the open may reset the device once)")

    wit = None
    if a.witness != "none":
        wit = Witness(a.witness, a.interval, tl, os.path.join(out, "counters.jsonl"))
        wit.start()

    legs, cur = [], None
    summary_path = os.path.join(out, "summary.md")

    def close_leg():
        nonlocal cur
        if cur is None:
            return
        cur.t1 = time.time()
        tl.put("MARK", f"end of leg '{cur.name}'")
        legs.append(cur)
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(leg_summary(cur, tl, wit) + "\n")
        print(f"(summary of '{cur.name}' appended to {summary_path})", flush=True)
        cur = None

    def handle(line):
        nonlocal cur
        line = line.strip()
        if not line:
            return True
        if line in ("q", "quit", "exit"):
            return False
        if line.startswith("start "):
            close_leg()
            cur = Leg(line[6:].strip(), time.time(), wit.last if wit else None)
            tl.put("MARK", f"start of leg '{cur.name}'")
            return True
        if line == "end":
            close_leg()
            return True
        if line == "ring":
            txt = get(a.witness, "/api/logs/ring", timeout=8, raw=True) if wit else None
            if txt is None:
                tl.put("MARK", "ring: the witness did not answer")
            else:
                tl.put("MARK", f"ring: {len(txt.splitlines())} lines")
                with open(os.path.join(out, f"ring_{stamp()[:8].replace(':', '')}.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(ANSI.sub("", txt))
            return True
        tl.put("MARK", line)
        return True

    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"# App session capture: {a.session} ({date})\n\n"
                f"Witness {a.witness}, console {port}, folder `{out}`.\n\n")

    t_end = time.time() + a.secs if a.secs else None
    marks_path = a.marks_file or os.path.join(out, "marks_in.txt")
    open(marks_path, "a", encoding="utf-8").close()
    marks_pos = os.path.getsize(marks_path)
    tl.put("MARK", f"marks file {marks_path}")

    def poll_marks():
        """New lines of the marks file, as if typed. False when one said q."""
        nonlocal marks_pos
        try:
            size = os.path.getsize(marks_path)
            if size <= marks_pos:
                return True
            with open(marks_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(marks_pos)
                chunk = f.read()
            marks_pos += len(chunk.encode("utf-8"))
        except OSError:
            return True
        for line in chunk.splitlines():
            # a PowerShell Add-Content -Encoding utf8 puts a byte-order mark first
            if not handle(line.replace(chr(0xFEFF), "")):
                return False
        return True

    try:
        if a.no_stdin or not sys.stdin or not sys.stdin.isatty():
            if a.secs:
                # unattended with a duration: the run is one leg named after the session
                cur = Leg(a.session, time.time(), wit.last if wit else None)
                tl.put("MARK", f"start of leg '{cur.name}' (unattended)")
            while t_end is None or time.time() < t_end:
                if not poll_marks():
                    break
                time.sleep(0.5)
        else:
            print("commands: start <leg> | end | ring | q | any text = a mark", flush=True)
            while t_end is None or time.time() < t_end:
                if not poll_marks():
                    break
                try:
                    line = sys.stdin.readline()
                except KeyboardInterrupt:
                    break
                if not line or not handle(line):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        close_leg()
        if wit:
            txt = get(a.witness, "/api/logs/ring", timeout=10, raw=True)
            if txt is not None:
                with open(os.path.join(out, "ring_end.txt"), "w", encoding="utf-8") as f:
                    f.write(ANSI.sub("", txt))
            wit.stop()
        if tail:
            tail.stop()
        tl.put("MARK", "session ends")
        tl.close()
        print(f"APP SESSION CAPTURE DONE legs={len(legs)} folder={out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
