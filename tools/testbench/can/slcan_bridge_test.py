#!/usr/bin/env python3
"""slcan over TCP, end to end, and the trap first -> SLCAN BRIDGE PASS.

Born from a Discord report (2026-10-16): "SLCAN works on the CAN monitor,
not over python-can; socket_manager says client connected, nothing
arrives". On the bench that is a server switched on with no connection
row: the socket accepts and discards. The gate provokes that state first,
then proves the bridged path with every client a user reaches for.

Legs (each check prints PASS/FAIL/SKIP; the verdict is the last line):
  T  the trap: `slcan0` enabled, no bridge on it (the as-found bench state,
     otherwise staged with one restart): /api/sockets says bridge null, a
     raw client with python-can's opening bytes reads nothing while PCAN
     puts frames on the bus and /api/can counts them, and the log ring
     gains the W line at the connect.
  B  bridged (one restart: br_slcan = can <-slcan-> slcan0 and
     br_can = can <-slcan-> ws_can, any bridge on ws_can parked): bridge
     names both; python-can through --slcan receives exactly N frames in
     order while a /ws/can client beside it receives the same N; python-can
     TX and a WS TX land on PCAN; both bridges pumped the same chunk count;
     0 drops at the server, the bridges and the controller.
  L  Linux on the Pi (--pi; SKIP with the reason when python-can or sudo is
     missing there): python-can straight at the DUT, and the manual's
     socat + slcand + SocketCAN route (candump, cansend).
  R  settings back as found (one restart), planned restarts only, E lines
     and unexpected resets unchanged.

  python slcan_bridge_test.py --dut localhost:18081 --slcan localhost:18333
         --dut-ip 10.42.1.194 --pi rpi001 [--pcan PCAN_USBBUS2] [--n 200]
         [--skip-pi]
test.ps1 slcan holds the tunnel (port 80 and 3333 of the DUT) and the
console. Needs python-can on the PC (the IDF venv) and, for leg L, on the
Pi (~/.local, 2026-10-16) plus sudo for slcand.
"""
import argparse
import collections
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from wsmin import WS  # noqa: E402

GEN_ID = 0x123
HELLO = b"C\rS6\r\rO\r"  # python-can 4.6 on open: close, S6, an empty line, O

fails = []
skips = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


def skip(name, why):
    print("SKIP: " + name + "  [" + why + "]", flush=True)
    skips.append(name)


class Dut:
    def __init__(self, base):
        self.base = "http://" + base

    def api(self, path, method="GET", body=None, timeout=10, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(errors="replace")
        if raw:
            return t
        return json.loads(t) if t.strip().startswith(("{", "[")) else t

    def get(self, path):
        try:
            return self.api(path)
        except Exception:
            return None

    def wait_up(self, secs=150):
        end = time.time() + secs
        while time.time() < end:
            try:
                self.api("/api/status", timeout=4)
                return True
            except Exception:
                time.sleep(2)
        return False

    def settings(self, comp):
        s = self.api("/api/settings/" + comp)
        s.pop("degraded", None)
        s.pop("pending_reboot", None)
        return s

    def ring_count(self, needle):
        try:
            t = self.api("/api/logs/ring", raw=True)
        except Exception:
            return -1
        return t.count(needle)


# ---- PCAN: the generator and the witness -----------------------------------------

class Pcan:
    def __init__(self, channel):
        import can
        self.can = can
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000)
        while self.bus.recv(timeout=0):
            pass
        self.seen = collections.defaultdict(list)   # id -> [data hex]
        self._halt = threading.Event()   # not `_stop`: Thread.join calls its own _stop()
        self._lock = threading.Lock()
        self._th = threading.Thread(target=self._watch, daemon=True)
        self._th.start()

    def _watch(self):
        while not self._halt.is_set():
            m = self.bus.recv(timeout=0.2)
            if m is not None and m.arbitration_id != GEN_ID:
                with self._lock:
                    self.seen[m.arbitration_id].append(m.data.hex())

    def clear_seen(self):
        with self._lock:
            self.seen.clear()

    def saw(self, can_id, data_hex=None, within=2.0):
        end = time.time() + within
        while time.time() < end:
            with self._lock:
                lst = list(self.seen.get(can_id, []))
            if lst and (data_hex is None or data_hex in lst):
                return True
            time.sleep(0.05)
        return False

    def generate(self, n, fps=100.0):
        """Put exactly n frames of GEN_ID on the bus (counter in bytes 0..1)."""
        sent = 0
        period = 1.0 / fps
        for i in range(n):
            m = self.can.Message(arbitration_id=GEN_ID, is_extended_id=False,
                                 data=[i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0x55, 0, 0, 0, 0])
            try:
                self.bus.send(m, timeout=0.5)
                sent += 1
            except Exception as e:
                print("pcan: send error:", e, flush=True)
                break
            time.sleep(period)
        return sent

    def close(self):
        self._halt.set()
        time.sleep(0.3)
        self.bus.shutdown()


# ---- the slcan clients on the PC -------------------------------------------------

def counters_ok(lst, n):
    """exactly n counters, strictly increasing from 0"""
    return len(lst) == n and lst == list(range(n))


class PyCanClient(threading.Thread):
    """python-can's own slcan interface over socket://, collecting GEN_ID counters."""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        import can
        self.can = can
        self.bus = can.Bus(interface="slcan", channel="socket://%s:%d" % (host, port), bitrate=500000)
        self.got = []
        self.other = collections.Counter()
        self._halt = threading.Event()
        self.start()

    def run(self):
        while not self._halt.is_set():
            m = self.bus.recv(timeout=0.2)
            if m is None:
                continue
            if m.arbitration_id == GEN_ID:
                self.got.append(m.data[0] | (m.data[1] << 8))
            else:
                self.other[m.arbitration_id] += 1

    def send(self, can_id, data):
        self.bus.send(self.can.Message(arbitration_id=can_id, is_extended_id=False, data=data))

    def close(self):
        self._halt.set()
        self.join(timeout=2)
        self.bus.shutdown()


class WsClient(threading.Thread):
    """the CAN monitor's path: slcan lines over /ws/can (wsmin)."""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.ws = WS(host, port, "/ws/can", timeout=0.5)
        self.got = []
        self.buf = b""
        self._halt = threading.Event()
        self.start()

    def run(self):
        while not self._halt.is_set():
            try:
                op, payload = self.ws.recv_frame()
            except socket.timeout:
                continue
            except Exception:
                break
            if not payload:
                continue
            self.buf += payload
            while b"\r" in self.buf:
                line, self.buf = self.buf.split(b"\r", 1)
                if line.startswith(b"t123"):
                    try:
                        d = bytes.fromhex(line[5:].decode())
                        self.got.append(d[0] | (d[1] << 8))
                    except Exception:
                        pass

    def send_line(self, line):
        self.ws.send(line)

    def close(self):
        self._halt.set()
        self.join(timeout=2)
        try:
            self.ws.s.close()
        except Exception:
            pass


def raw_probe(host, port, secs):
    """plain TCP: python-can's opening bytes, then read for secs"""
    s = socket.create_connection((host, port), timeout=5)
    s.settimeout(0.5)
    s.sendall(HELLO)
    got = b""
    end = time.time() + secs
    while time.time() < end:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            got += chunk
        except socket.timeout:
            pass
    s.close()
    return got


# ---- the Linux clients on the Pi ---------------------------------------------------

PI_PYCAN = r'''
import can, sys, time
host, port, n, secs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
bus = can.Bus(interface="slcan", channel="socket://%s:%d" % (host, port), bitrate=500000)
print("READY", flush=True)
got = []
end = time.time() + secs
while time.time() < end and len(got) < n:
    m = bus.recv(timeout=0.2)
    if m is not None and m.arbitration_id == 0x123:
        got.append(m.data[0] | (m.data[1] << 8))
bus.send(can.Message(arbitration_id=0x324, is_extended_id=False, data=[0x24, 0x24]))
time.sleep(0.5)
bus.shutdown()
print("RESULT %d %d" % (len(got), 1 if got == list(range(len(got))) else 0), flush=True)
'''

PI_SLCAND = r'''
DUT=$1; N=$2; SECS=$3
OUT=/tmp/slcan_gate_candump.out
sudo -n ip link set can0 down 2>/dev/null; sudo -n pkill -x slcand 2>/dev/null
sudo -n pkill -f "socat pty,link=/dev/netcan0" 2>/dev/null; sleep 0.5; sudo -n rm -f /dev/netcan0 $OUT
sudo -n socat pty,link=/dev/netcan0,raw,echo=0 tcp:$DUT:3333 &
for i in $(seq 1 20); do [ -e /dev/netcan0 ] && break; sleep 0.25; done
[ -e /dev/netcan0 ] || { echo "RESULT fail socat"; exit 0; }
sudo -n slcand -o -c -s6 /dev/netcan0 can0 || { echo "RESULT fail slcand"; exit 0; }
sleep 0.5
sudo -n ip link set can0 up || { echo "RESULT fail link"; exit 0; }
( timeout $SECS candump -n $N can0 > $OUT 2>&1; echo CANDUMP_DONE >> $OUT ) &
sleep 0.4; echo READY
while ! grep -q CANDUMP_DONE $OUT; do sleep 0.2; done
cansend can0 325#2525
sleep 0.5
echo "RESULT $(grep -c ' 123 ' $OUT) ok"
sudo -n ip link set can0 down; sudo -n pkill -x slcand; sudo -n pkill -f "socat pty,link=/dev/netcan0"; sudo -n rm -f /dev/netcan0
'''


def pi_ready(pi):
    """python-can and sudo on the Pi, or the reason they are not"""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", pi,
                            "python3 -c 'import can; print(can.__version__)' 2>&1; sudo -n true && echo SUDO_OK; which slcand socat candump cansend | wc -l"],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        return None, "ssh to the Pi failed: %s" % e
    out = r.stdout.strip().splitlines()
    if not out or "No module" in r.stdout:
        return None, "python-can missing on the Pi (pip3 install --user --break-system-packages python-can)"
    if "SUDO_OK" not in r.stdout:
        return out[0], "sudo -n refused on the Pi: the slcand route needs root"
    if out[-1].strip() != "4":
        return out[0], "can-utils or socat missing on the Pi"
    return out[0], ""


def pi_leg(pi, script_text, interp, args, pcan, n, fps, timeout_s=40):
    """scp a client script to the Pi, run it, generate n frames after READY,
       return (result_words, stdout_tail)"""
    suffix = ".py" if interp == "python3" else ".sh"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, newline="\n") as f:
        f.write(script_text)
        local = f.name
    remote = "/tmp/slcan_gate" + suffix
    try:
        subprocess.run(["scp", "-q", local, pi + ":" + remote], check=True, timeout=30)
    finally:
        os.unlink(local)
    cmd = ["ssh", "-o", "BatchMode=yes", pi, interp + " " + remote + " " + " ".join(str(a) for a in args)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    result = None
    ready = False
    deadline = time.time() + timeout_s

    def reader():
        for line in p.stdout:
            lines.append(line.rstrip())

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while time.time() < deadline:
        if not ready and any(l.strip() == "READY" for l in lines):
            ready = True
            time.sleep(0.3)
            pcan.generate(n, fps)
        res = [l for l in lines if l.startswith("RESULT")]
        if res:
            result = res[-1].split()
            break
        if p.poll() is not None and not any(l.startswith("RESULT") for l in lines):
            break
        time.sleep(0.1)
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    return result, lines[-6:]


# ---- the legs ------------------------------------------------------------------------

def bridges_on(bridges, jack):
    return [b for b in bridges if b.get("enabled") is not False and jack in (b.get("a"), b.get("b"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081", help="the DUT's web API (host:port, a tunnel is fine)")
    ap.add_argument("--slcan", default="localhost:18333", help="the DUT's slcan server (host:port, the tunnel's second forward)")
    ap.add_argument("--dut-ip", default="10.42.1.194", help="the DUT's address as the Pi sees it (leg L)")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--skip-pi", action="store_true")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--fps", type=float, default=100.0)
    a = ap.parse_args()

    dut = Dut(a.dut)
    sl_host, sl_port = a.slcan.split(":")[0], int(a.slcan.split(":")[1])
    ws_host, ws_port = a.dut.split(":")[0], int(a.dut.split(":")[1]) if ":" in a.dut else 80
    t0 = time.time()
    planned = 0

    # ---- preflight --------------------------------------------------------------
    st = dut.get("/api/status")
    if not st:
        print("SLCAN BRIDGE FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0 = st.get("boot_count")
    unexp0 = st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("socket_manager", "bridge_manager", "can_manager")}
    print("as found: servers=%s bridges=%s can=%s" % (
        [(s["name"], s["enabled"]) for s in found["socket_manager"]["servers"]],
        [(b["name"], b["a"], b["b"], b.get("enabled")) for b in found["bridge_manager"]["bridges"]],
        {k: found["can_manager"].get(k) for k in ("enabled", "baud", "silent")}), flush=True)
    try:
        pcan = Pcan(a.pcan)
    except Exception as e:
        print("SLCAN BRIDGE FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)

    def submit(sock=None, br=None, can=None, why=""):
        nonlocal planned
        changed = False
        for comp, obj in (("socket_manager", sock), ("bridge_manager", br), ("can_manager", can)):
            if obj is None:
                continue
            r = dut.api("/api/settings/" + comp, "PUT", obj)
            changed = changed or bool(r.get("changed", True))
        r = dut.api("/api/settings/submit", "POST")
        if r.get("reboot"):
            planned += 1
            print("restart %d (%s)..." % (planned, why), flush=True)
            time.sleep(6)
            if not dut.wait_up():
                print("SLCAN BRIDGE FAIL: the DUT did not come back after the restart (%s)" % why)
                sys.exit(1)
            time.sleep(2)
        return changed

    # ---- leg T: the trap ---------------------------------------------------------
    print("== T: the server with no connection row", flush=True)
    sock = json.loads(json.dumps(found["socket_manager"]))
    br = json.loads(json.dumps(found["bridge_manager"]))
    can = json.loads(json.dumps(found["can_manager"]))
    srv = next((s for s in sock["servers"] if s["name"] == "slcan0"), None)
    if srv is None:
        srv = {"name": "slcan0", "proto": "tcp", "port": 3333, "enabled": True}
        sock["servers"].append(srv)
    srv["enabled"] = True
    srv["proto"] = "tcp"
    srv["port"] = 3333
    for b in bridges_on(br["bridges"], "slcan0"):
        b["enabled"] = False
    can["enabled"] = True
    can["baud"] = "500"
    can["silent"] = False
    need = (sock != found["socket_manager"] or br != found["bridge_manager"] or can != found["can_manager"])
    if need:
        print("staging the trap state (slcan0 on, nothing bridged to it, CAN 500k)", flush=True)
        submit(sock, br, can, why="the trap state")
    else:
        print("the as-found state is the trap already: no restart", flush=True)

    sk = dut.get("/api/sockets") or {}
    row = next((s for s in sk.get("servers", []) if s["name"] == "slcan0"), None)
    check("T1 /api/sockets reports slcan0 up with bridge null", row is not None and row.get("up") is True and "bridge" in row and row["bridge"] is None,
          row and {"up": row.get("up"), "bridge": row.get("bridge", "MISSING: firmware before 2026-10-16?")})
    needle = "socket_manager: slcan0: no connection uses this server"
    ring_before = dut.ring_count(needle)
    can_before = (dut.get("/api/can") or {}).get("rx", 0)
    got = [None]

    def probe():
        got[0] = raw_probe(sl_host, sl_port, 3.5)

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    time.sleep(0.8)
    sent = pcan.generate(20, 50)
    th.join(timeout=8)
    can_after = (dut.get("/api/can") or {}).get("rx", 0)
    check("T2 the bus saw the %d frames (/api/can rx)" % sent, can_after - can_before >= sent, "rx +%d" % (can_after - can_before))
    check("T3 the client read nothing", got[0] == b"", got[0][:40] if got[0] else got[0])
    ring_after = dut.ring_count(needle)
    check("T4 the log ring gained the W line at the connect", ring_after > ring_before >= 0, "%d -> %d" % (ring_before, ring_after))

    # the DUT is reconfigured from here on: whatever happens, leg R puts the
    # settings back (a bench that dies dirty makes the next run take the
    # dirty state as "as found")
    try:
        legs_bridged(a, dut, pcan, found, br, submit, sl_host, sl_port, ws_host, ws_port)
    except Exception as e:
        check("the bridged legs ran to the end", False, "%s: %s" % (type(e).__name__, e))

    # ---- leg R: restore -----------------------------------------------------------------
    print("== R: restore", flush=True)
    submit(found["socket_manager"], found["bridge_manager"], found["can_manager"], why="the settings as found")
    back = {c: dut.settings(c) for c in ("socket_manager", "bridge_manager", "can_manager")}
    check("R1 settings back as found", back == found,
          {c: "same" if back[c] == found[c] else "DIFFERS" for c in found})
    st = dut.get("/api/status") or {}
    check("R2 only the planned restarts (%d)" % planned, st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
          {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
    err1 = (st.get("health") or {}).get("log_errors")
    check("R3 no new E lines", err1 == err0, (err0, err1))
    pcan.close()

    print("%d checks, %d failed, %d skipped, %.0f s" % (len(passes) + len(fails), len(fails), len(skips), time.time() - t0), flush=True)
    if fails:
        print("SLCAN BRIDGE FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("SLCAN BRIDGE PASS" + (" (Pi legs skipped)" if skips else ""))


def legs_bridged(a, dut, pcan, found, br, submit, sl_host, sl_port, ws_host, ws_port):
    # ---- leg B: bridged ----------------------------------------------------------
    print("== B: the connection rows (br_slcan, br_can)", flush=True)
    br2 = json.loads(json.dumps(br))
    for b in br2["bridges"]:
        if "ws_can" in (b.get("a"), b.get("b")) or b["name"] in ("br_slcan", "br_can"):
            b["enabled"] = False
    br2["bridges"] = [b for b in br2["bridges"] if b["name"] not in ("br_slcan", "br_can")]
    br2["bridges"].append({"name": "br_can", "a": "can", "b": "ws_can", "translator": "slcan", "enabled": True})
    br2["bridges"].append({"name": "br_slcan", "a": "can", "b": "slcan0", "translator": "slcan", "enabled": True})
    if len(br2["bridges"]) > 6:
        raise RuntimeError("no free bridge slot for the rows (%d configured)" % len(br2["bridges"]))
    submit(br=br2, why="the connection rows")

    bl = dut.get("/api/bridges") or {}
    up = {b["name"]: b.get("up") for b in bl.get("bridges", [])}
    check("B1 br_slcan and br_can up", up.get("br_slcan") is True and up.get("br_can") is True, up)
    sk = dut.get("/api/sockets") or {}
    row = next((s for s in sk.get("servers", []) if s["name"] == "slcan0"), {})
    wsl = dut.get("/api/ws") or {}
    wrow = next((c for c in wsl.get("channels", []) if c["name"] == "ws_can"), {})
    check("B2 /api/sockets and /api/ws name the bridges", row.get("bridge") == "br_slcan" and wrow.get("bridge") == "br_can",
          {"slcan0": row.get("bridge"), "ws_can": wrow.get("bridge")})

    stats0 = {s["name"]: s.get("stats", {}) for s in sk.get("servers", [])}
    can0 = dut.get("/api/can") or {}
    pyc = PyCanClient(sl_host, sl_port)
    wsc = WsClient(ws_host, ws_port)
    time.sleep(2.5)  # python-can's sleep_after_open
    pcan.clear_seen()
    sent = pcan.generate(a.n, a.fps)
    time.sleep(2.0)
    check("B3 python-can over TCP received exactly N=%d in order" % sent, counters_ok(pyc.got, sent),
          "%d frames, first %s last %s" % (len(pyc.got), pyc.got[:3], pyc.got[-3:]))
    check("B4 the /ws/can monitor beside it received the same N", counters_ok(wsc.got, sent), "%d frames" % len(wsc.got))
    pyc.send(0x321, [1, 2, 3, 4])
    check("B5 python-can TX reached the bus (0x321)", pcan.saw(0x321, "01020304"), dict(pcan.seen))
    wsc.send_line(b"t3222ABCD\r")
    check("B6 a WS TX reached the bus (0x322)", pcan.saw(0x322, "abcd"), dict(pcan.seen))
    time.sleep(0.5)
    pyc.close()
    wsc.close()
    bl = dut.get("/api/bridges") or {}
    bs = {b["name"]: b.get("stats", {}) for b in bl.get("bridges", [])}
    s_sl, s_can = bs.get("br_slcan", {}), bs.get("br_can", {})
    check("B7 both bridges pumped the same chunks (the can jack fan-out)",
          s_sl.get("a2b_chunks", -1) == s_can.get("a2b_chunks", -2) and s_sl.get("a2b_chunks", 0) > 0,
          {"br_slcan": s_sl.get("a2b_chunks"), "br_can": s_can.get("a2b_chunks")})
    check("B8 no send or codec errors on the bridges",
          all(s.get("send_errors", 1) == 0 and s.get("codec_errors", 1) == 0 for s in (s_sl, s_can)),
          {"br_slcan": s_sl, "br_can": s_can})
    sk = dut.get("/api/sockets") or {}
    row = next((s for s in sk.get("servers", []) if s["name"] == "slcan0"), {})
    st1 = row.get("stats", {})
    can1 = dut.get("/api/can") or {}
    check("B9 no drops at the server or the controller",
          st1.get("rx_drops", 1) == 0 and st1.get("tx_drops", 1) == 0 and
          can1.get("dispatch_drops", 1) - can0.get("dispatch_drops", 0) == 0 and
          can1.get("rx_missed", 1) - can0.get("rx_missed", 0) == 0,
          {"server": st1, "dispatch_drops": can1.get("dispatch_drops"), "rx_missed": can1.get("rx_missed")})
    check("B10 the server's bytes_out moved", st1.get("bytes_out", 0) > stats0.get("slcan0", {}).get("bytes_out", 0), st1.get("bytes_out"))

    # ---- leg L: Linux on the Pi -------------------------------------------------------
    print("== L: the Linux clients on %s" % a.pi, flush=True)
    if a.skip_pi:
        skip("L python-can on the Pi", "--skip-pi")
        skip("L slcand + SocketCAN on the Pi", "--skip-pi")
    else:
        ver, why = pi_ready(a.pi)
        if ver is None:
            skip("L python-can on the Pi", why)
            skip("L slcand + SocketCAN on the Pi", why)
        else:
            n2 = max(50, a.n // 2)
            pcan.clear_seen()
            res, tail = pi_leg(a.pi, PI_PYCAN, "python3", [a.dut_ip, 3333, n2, 12], pcan, n2, a.fps)
            ok = res is not None and len(res) == 3 and res[1] == str(n2) and res[2] == "1"
            check("L1 python-can %s on the Pi straight at the DUT: exactly N=%d in order" % (ver, n2), ok, res or tail)
            check("L2 the Pi's python-can TX reached the bus (0x324)", pcan.saw(0x324, "2424"), dict(pcan.seen))
            if why:
                skip("L slcand + SocketCAN on the Pi", why)
            else:
                pcan.clear_seen()
                res, tail = pi_leg(a.pi, PI_SLCAND, "bash", [a.dut_ip, n2, 15], pcan, n2, a.fps, timeout_s=60)
                ok = res is not None and len(res) == 3 and res[1] == str(n2)
                check("L3 socat + slcand + candump on the Pi: N=%d frames" % n2, ok, res or tail)
                check("L4 cansend from can0 reached the bus (0x325)", pcan.saw(0x325, "2525"), dict(pcan.seen))


if __name__ == "__main__":
    main()
