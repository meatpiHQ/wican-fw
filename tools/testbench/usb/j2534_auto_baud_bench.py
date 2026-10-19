#!/usr/bin/env python3
"""A tester states the bit rate on a silent bus -> J2534 AUTO BAUD PASS.

The second half of a Discord report (2026-10-17): "J2534 driver works,
communication established, no comm to the canbus". With `can_manager.baud =
auto` on a bus that speaks only when asked (the bench simulator, a gatewayed
OBD port) nothing was ever proven, the node stayed listen-only and every
write was refused without a word. Now a J2534 CONNECT's baud and an slcan
client's `S` command are taken when the bus is silent, and a refusal says so.

Legs (each check prints PASS/FAIL; the verdict is the last line):
  C  configure (one restart): j2534_server on with allow_lan, the slcan
     bridge `br_slcan` beside the monitor's, can_manager `auto`, autopid's
     group paused for the run so the bus stays quiet.
  R  the refusal, said: CONNECT(CAN, 20000) on the detecting node is not a
     rate it knows, so the channel opens and the WRITE answers ERR_FAILED;
     /api/j2534 tx_refused steps, the ring gains the j2534 and can_core W lines.
  A  the adoption: CONNECT(CAN, 500000) is taken (/api/can adopted, running,
     not listen-only, not verified); WRITE 7DF goes out (PCAN sees it, the
     simulator's 7E8 comes back as RX_MSG); two PCAN frames then prove the
     rate (verified); ISO15765 CONNECT + FLOW_CONTROL + 10 02 -> 50 02;
     a CONNECT at 250000 is now ERR_INVALID_BAUDRATE (the bus has its rate).
  S  after a plain restart (the node detecting again): an slcan client's
     "S6\\rO\\r" through br_slcan is taken the same way, and its t7DF line
     reaches the bus.
  X  settings back as found (one restart), planned restarts only, E lines
     and unexpected resets unchanged.

  python j2534_auto_baud_bench.py --dut localhost:18081 --j2534 localhost:18534
         --slcan localhost:18333 [--pcan PCAN_USBBUS2]
test.ps1 j2534auto holds the tunnel (ports 80, 6809 and 3333 of the DUT) and
the console. Needs python-can on the PC (the IDF venv) and the simulator
answering 7DF on the bench bus.
"""
import argparse
import collections
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_argv = sys.argv
sys.argv = [sys.argv[0]]
import j2534_bench as b  # noqa: E402  (its module-level argv parsing wants no args)
sys.argv = _argv

GEN_ID = 0x123
START_FILTER = 0x11   # J2534_MT_START_FILTER
ERR_FAILED = 0x07     # J2534 STATUS: the write the node refused answers this
RING_J2534 = "j2534_server"
RING_REFUSED_J2534 = "write refused: the CAN node may not talk yet"
RING_REFUSED_CORE = "can_core: transmit refused: the node may not talk"
RING_ADOPTED = "bitrate stated by the tester"

fails = []
passes = []


def check(name, ok, detail=""):
    print(("PASS: " if ok else "FAIL: ") + name + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
    (passes if ok else fails).append(name)
    return ok


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
            return self.api("/api/logs/ring", raw=True).count(needle)
        except Exception:
            return -1

    def can(self, *keys):
        c = self.get("/api/can") or {}
        return {k: c.get(k) for k in keys} if keys else c


class Pcan:
    def __init__(self, channel):
        import can
        self.can = can
        self.bus = can.Bus(interface="pcan", channel=channel, bitrate=500000)
        while self.bus.recv(timeout=0):
            pass
        self.seen = collections.defaultdict(list)
        self._halt = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while not self._halt.is_set():
            m = self.bus.recv(timeout=0.2)
            if m is not None and not m.is_error_frame:
                with self._lock:
                    self.seen[m.arbitration_id].append(m.data.hex())

    def clear(self):
        with self._lock:
            self.seen.clear()

    def saw(self, can_id, prefix=None, within=2.0):
        end = time.time() + within
        while time.time() < end:
            with self._lock:
                lst = list(self.seen.get(can_id, []))
            if lst and (prefix is None or any(d.startswith(prefix) for d in lst)):
                return True
            time.sleep(0.05)
        return False

    def generate(self, n, fps=50.0):
        for i in range(n):
            self.bus.send(self.can.Message(arbitration_id=GEN_ID, is_extended_id=False,
                                           data=[i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0x55, 0, 0, 0, 0]), timeout=0.5)
            time.sleep(1.0 / fps)

    def close(self):
        self._halt.set()
        time.sleep(0.3)
        self.bus.shutdown()


def write_and_collect(c, channel, payload, secs):
    """WRITE_MSGS and every RX_MSG from that instant (Client.call drops the
    RX_MSG that land before the ACK, and the simulator answers in ms)."""
    c.seq = (c.seq + 1) & 0xFFFF
    c.s.sendall(b.HDR.pack(b.MAGIC, 1, b.WRITE_MSGS, c.seq, channel, len(payload)) + payload)
    c.s.settimeout(0.5)
    got, ack = [], None
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            rt, rseq, rch, body = c._read_frame()
        except Exception:
            break
        if rt == b.ACK:
            ack = struct.unpack_from("<I", body, 0)[0]
        elif rt == b.RX_MSG:
            size = b.MSG.unpack_from(body, 0)[5]
            d = body[b.MSG.size:b.MSG.size + size]
            got.append((struct.unpack(">I", d[:4])[0], d[4:].hex()))
    c.s.settimeout(5)
    return ack, got


def can_write(c, ch, can_id, data):
    d = struct.pack(">I", can_id) + bytes(data)
    msg = b.MSG.pack(b.PROT_CAN, 0, 0, 0, 0, len(d)) + d
    return write_and_collect(c, ch, struct.pack("<I", 1) + msg, 1.5)


def connect(c, proto, baud):
    st, ch, _ = c.call(b.CONNECT, payload=struct.pack("<III", proto, 0, baud))
    return st, ch


def slcan_probe(host, port, secs=2.0):
    s = socket.create_connection((host, port), timeout=5)
    s.settimeout(0.5)
    s.sendall(b"S6\rO\r")      # python-can's statement of the bitrate, then open
    time.sleep(1.0)            # the node bounces to the stated rate
    s.sendall(b"t7DF80201000000000000\r")   # 7DF, dlc 8: 02 01 00 and padding
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--j2534", default="localhost:18534")
    ap.add_argument("--slcan", default="localhost:18333")
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    a = ap.parse_args()

    dut = Dut(a.dut)
    jh, jp = a.j2534.split(":")[0], int(a.j2534.split(":")[1])
    sh, sp = a.slcan.split(":")[0], int(a.slcan.split(":")[1])
    t0 = time.time()
    planned = 0

    st = dut.get("/api/status")
    if not st:
        print("J2534 AUTO BAUD FAIL: no DUT at", a.dut)
        sys.exit(1)
    boot0, unexp0 = st.get("boot_count"), st.get("unexpected_resets")
    err0 = (st.get("health") or {}).get("log_errors")
    print("dut: %s boot_count=%s unexpected=%s log_errors=%s" % (st.get("version"), boot0, unexp0, err0), flush=True)
    found = {c: dut.settings(c) for c in ("j2534_server", "can_manager", "bridge_manager", "socket_manager")}
    groups = (dut.get("/api/autopid") or {}).get("groups", [])
    group_on = [g["name"] for g in groups if g.get("enabled")]
    print("as found: j2534=%s can=%s groups on=%s" % (
        {k: found["j2534_server"].get(k) for k in ("enabled", "allow_lan")},
        {k: found["can_manager"].get(k) for k in ("enabled", "baud", "silent")}, group_on), flush=True)
    try:
        pcan = Pcan(a.pcan)
    except Exception as e:
        print("J2534 AUTO BAUD FAIL: PCAN %s not usable: %s" % (a.pcan, e))
        sys.exit(1)

    def submit(objs, why):
        nonlocal planned
        for comp, obj in objs.items():
            dut.api("/api/settings/" + comp, "PUT", obj)
        r = dut.api("/api/settings/submit", "POST")
        if r.get("reboot"):
            planned += 1
            print("restart %d (%s)..." % (planned, why), flush=True)
            time.sleep(6)
            if not dut.wait_up():
                print("J2534 AUTO BAUD FAIL: the DUT did not come back (%s)" % why)
                sys.exit(1)
            time.sleep(2)

    def pause_autopid(on):
        for g in group_on:
            try:
                dut.api("/api/autopid/group", "POST", {"name": g, "enabled": on})
            except Exception:
                pass

    # ---- C: configure ---------------------------------------------------------
    print("== C: configure", flush=True)
    j = json.loads(json.dumps(found["j2534_server"]))
    j["enabled"] = True
    j["allow_lan"] = True
    can = json.loads(json.dumps(found["can_manager"]))
    can["enabled"] = True
    can["baud"] = "auto"
    can["silent"] = False
    sock = json.loads(json.dumps(found["socket_manager"]))
    srv = next((s for s in sock["servers"] if s["name"] == "slcan0"), None)
    if srv is None:
        sock["servers"].append({"name": "slcan0", "proto": "tcp", "port": 3333, "enabled": True})
    else:
        srv["enabled"] = True
    br = json.loads(json.dumps(found["bridge_manager"]))
    br["bridges"] = [x for x in br["bridges"] if x["name"] != "br_slcan"]
    for x in br["bridges"]:
        if "slcan0" in (x.get("a"), x.get("b")):
            x["enabled"] = False
    br["bridges"].append({"name": "br_slcan", "a": "can", "b": "slcan0", "translator": "slcan", "enabled": True})
    if len(br["bridges"]) > 6:
        print("J2534 AUTO BAUD FAIL: no free bridge slot for br_slcan")
        sys.exit(1)
    submit({"j2534_server": j, "can_manager": can, "socket_manager": sock, "bridge_manager": br}, "the configuration")
    pause_autopid(False)
    time.sleep(1.5)

    try:
        planned += legs(dut, pcan, jh, jp, sh, sp)   # leg S restarts the DUT once
    except Exception as e:
        check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))

    # ---- X: restore --------------------------------------------------------------
    print("== X: restore", flush=True)
    submit(found, "the settings as found")
    pause_autopid(True)
    back = {c: dut.settings(c) for c in found}
    check("X1 settings back as found", back == found, {c: "same" if back[c] == found[c] else "DIFFERS" for c in found})
    st = dut.get("/api/status") or {}
    check("X2 only the planned restarts (%d)" % planned, st.get("boot_count") == boot0 + planned and st.get("unexpected_resets") == unexp0,
          {"boot_count": (boot0, st.get("boot_count")), "unexpected": (unexp0, st.get("unexpected_resets"))})
    err1 = (st.get("health") or {}).get("log_errors")
    check("X3 no new E lines", err1 == err0, (err0, err1))
    pcan.close()

    print("%d checks, %d failed, %.0f s" % (len(passes) + len(fails), len(fails), time.time() - t0), flush=True)
    if fails:
        print("J2534 AUTO BAUD FAIL: " + ", ".join(fails))
        sys.exit(1)
    print("J2534 AUTO BAUD PASS")


def legs(dut, pcan, jh, jp, sh, sp):
    """R, A and S; returns the number of plain restarts it asked for (leg S)"""
    restarts = 0
    # ---- R: the refusal, said ------------------------------------------------------
    print("== R: a rate the node does not know is refused, and said", flush=True)
    c0 = dut.can("state", "listen_only", "verified", "adopted", "baud_auto", "tx_refused")
    check("R1 the node is detecting, listen-only, on a quiet bus", c0.get("state") == "detecting" and c0.get("listen_only") is True and c0.get("baud_auto") is True, c0)
    ring_j0 = dut.ring_count(RING_REFUSED_J2534)
    ring_c0 = dut.ring_count(RING_REFUSED_CORE)
    j0 = (dut.get("/api/j2534") or {}).get("tx_refused")
    check("R2 /api/j2534 carries tx_refused", j0 is not None, j0)
    cl = b.Client(jh, jp)
    check("R3 HELLO and OPEN", cl.call(b.HELLO)[0] == 0 and cl.call(b.OPEN)[0] == 0)
    st, ch = connect(cl, b.PROT_CAN, 20000)
    check("R4 CONNECT(CAN, 20000) opens a channel (not a rate this node knows: nothing adopted)", st == 0 and ch == 1, (hex(st), ch))
    c1 = dut.can("state", "adopted", "listen_only")
    check("R5 the node is still detecting and listen-only", c1.get("state") == "detecting" and not c1.get("adopted") and c1.get("listen_only") is True, c1)
    pcan.clear()
    ack, got = can_write(cl, ch, 0x7DF, [2, 1, 0, 0, 0, 0, 0, 0])
    check("R6 the WRITE is refused (ERR_FAILED), nothing on the bus", ack == ERR_FAILED and not pcan.saw(0x7DF, within=0.5), (hex(ack) if ack is not None else ack, dict(pcan.seen)))
    j1 = (dut.get("/api/j2534") or {}).get("tx_refused")
    check("R7 tx_refused stepped", j1 is not None and j0 is not None and j1 == j0 + 1, (j0, j1))
    time.sleep(0.5)
    check("R8 the ring says why (j2534 and can_core W lines)", dut.ring_count(RING_REFUSED_J2534) > ring_j0 and dut.ring_count(RING_REFUSED_CORE) > ring_c0,
          {"j2534": (ring_j0, dut.ring_count(RING_REFUSED_J2534)), "core": (ring_c0, dut.ring_count(RING_REFUSED_CORE))})
    cl.call(b.DISCONNECT, channel=ch)

    # ---- A: the adoption ----------------------------------------------------------------
    print("== A: CONNECT at 500000 is taken on the silent bus", flush=True)
    ring_a0 = dut.ring_count(RING_ADOPTED)
    st, ch = connect(cl, b.PROT_CAN, 500000)
    check("A1 CONNECT(CAN, 500000) accepted", st == 0 and ch == 1, (hex(st), ch))
    time.sleep(0.5)
    c2 = dut.can("state", "adopted", "verified", "listen_only", "baud_kbps", "baud_auto")
    check("A2 /api/can: running, adopted, not verified, not listen-only, 500 kbit/s",
          c2.get("state") == "running" and c2.get("adopted") is True and c2.get("verified") is False and c2.get("listen_only") is False and c2.get("baud_kbps") == 500, c2)
    check("A3 the ring says the bitrate was stated by the tester", dut.ring_count(RING_ADOPTED) > ring_a0, (ring_a0, dut.ring_count(RING_ADOPTED)))
    pcan.clear()
    tx0 = dut.can("tx").get("tx")
    ack, got = can_write(cl, ch, 0x7DF, [2, 1, 0, 0, 0, 0, 0, 0])
    on_7e8 = [d for cid, d in got if cid == 0x7E8]
    check("A4 the WRITE goes out and the simulator answers", ack == 0 and pcan.saw(0x7DF, "0201") and any(d.startswith("064100") for d in on_7e8),
          {"ack": hex(ack) if ack is not None else ack, "7E8": on_7e8[:2], "bus": {hex(k): v[:1] for k, v in pcan.seen.items()}})
    check("A5 /api/can tx stepped", dut.can("tx").get("tx") == tx0 + 1, (tx0, dut.can("tx").get("tx")))
    pcan.generate(2, 20)
    time.sleep(0.6)
    c3 = dut.can("verified", "adopted", "baud_detected", "state")
    check("A6 two frames prove the stated rate (verified, 500 detected)", c3.get("verified") is True and c3.get("baud_detected") == 500 and c3.get("state") == "running", c3)
    st, ch2 = connect(cl, b.PROT_ISO15765, 500000)
    check("A7 CONNECT(ISO15765) accepted", st == 0, hex(st))
    mask = b.MSG.pack(b.PROT_ISO15765, 0, 0, 0, 0, 4) + struct.pack(">I", 0xFFFFFFFF)
    patt = b.MSG.pack(b.PROT_ISO15765, 0, 0, 0, 0, 4) + struct.pack(">I", 0x7E8)
    flow = b.MSG.pack(b.PROT_ISO15765, 0, b.TX_PAD, 0, 0, 4) + struct.pack(">I", 0x7E0)
    st, fid, _ = cl.call(START_FILTER, ch2, struct.pack("<I", 3) + mask + patt + flow)
    check("A8 FLOW_CONTROL filter bound (7E0/7E8)", st == 0, hex(st))
    st, rx = cl.write_uds(ch2, bytes([0x10, 0x02]))
    check("A9 ISO15765 10 02 answered 50 02", st == 0 and any(m[:2] == b"\x50\x02" for m in rx), (hex(st), [m.hex() for m in rx][:2]))
    cl.call(b.DISCONNECT, channel=ch2)
    st, ch3 = connect(cl, b.PROT_CAN, 250000)
    check("A10 a CONNECT at 250000 is now ERR_INVALID_BAUDRATE (the bus has its rate)", st == b.ERR_INVALID_BAUDRATE, hex(st))
    cl.call(b.DISCONNECT, channel=ch)
    cl.call(b.CLOSE)
    cl.s.close()
    j2 = (dut.get("/api/j2534") or {}).get("tx_refused")
    check("A11 no further refusal counted", j2 == (dut.get("/api/j2534") or {}).get("tx_refused") and j2 is not None, j2)

    # ---- S: an slcan client's S6 after a restart --------------------------------------
    print("== S: after a restart, an slcan client states the rate", flush=True)
    try:
        dut.api("/api/restart", "POST")
        restarts += 1
    except Exception:
        pass
    time.sleep(6)
    if not dut.wait_up():
        raise RuntimeError("the DUT did not come back from the plain restart")
    time.sleep(2)
    c4 = dut.can("state", "adopted", "listen_only")
    check("S1 detecting again after the restart", c4.get("state") == "detecting" and not c4.get("adopted"), c4)
    ring_a1 = dut.ring_count(RING_ADOPTED)
    pcan.clear()
    got = slcan_probe(sh, sp)
    c5 = dut.can("state", "adopted", "listen_only", "baud_kbps")
    check("S2 S6 was taken: running at 500, adopted, not listen-only", c5.get("state") == "running" and c5.get("adopted") is True and c5.get("listen_only") is False and c5.get("baud_kbps") == 500, c5)
    check("S3 the slcan client's t7DF reached the bus and the answer came back", pcan.saw(0x7DF, "0201", within=0.5) and b"t7E8" in got, {"bus": {hex(k): v[:1] for k, v in pcan.seen.items()}, "got": got[:40]})
    check("S4 the ring says the bitrate was stated", dut.ring_count(RING_ADOPTED) > ring_a1, (ring_a1, dut.ring_count(RING_ADOPTED)))
    return restarts


if __name__ == "__main__":
    main()
