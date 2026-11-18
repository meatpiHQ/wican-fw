#!/usr/bin/env python3
"""The usb_obd jack as a pipe: how soon a short write is on its way, whether
every byte arrives, and whether a lost byte is counted -> USB OBD PIPE PASS.

`usb_obd` is the unit as a USB serial device (its USB-C cabled to this PC:
the CH342's second port, 2,000,000 baud). Its reader asked the UART driver
for 128 bytes with a 20 ms limit, and the driver waits the whole limit
again after every arrival, so a command of a few bytes left the jack 20 ms
after it came. And the bytes the UART lost while the unit wrote its flash
(the UART's interrupt waits for the write, its FIFO holds 128 bytes, 0.64 ms
at this speed) were in no counter at all.

For the run the jack is bridged raw to the TCP server `slcan0` (the factory
row usb_obd <-> obd is parked), so what the PC writes on the COM port comes
back on a TCP client and the other way round, and nothing reaches the OBD
chip.

Legs (each check prints PASS/FAIL; the verdict is the last line):
  L  40 short writes each way, one at a time. COM -> TCP is the reader's
     path, TCP -> COM is printed beside it as the measure of the rest of the
     way (WiFi, the tunnel, the USB adapter).
     L1 the fastest COM -> TCP took less than 20 ms.
  V  60,000 bytes COM -> TCP at 50,000 bytes a second, as numbered lines,
     with the unit not writing its flash: the leg starts when the unit has
     been up 30 s, and a pass that lost lines while the flash counters of
     /api/status moved is made again (that loss is leg F's subject).
     V1 every line arrives once and in order, no loss counter moved.
  F  the same stream for 6 s while three files of 32 KB are written to the
     unit's flash.
     F1 the flash was written meanwhile and lines were lost.
     F2 the loss is on /api/bridges: `losses.a` of the bridge on usb_obd
        (rx_overruns, rx_drops) or the TCP server's tx_drops moved.
  R  settings back as found (one restart), planned restarts only, no reset
     of its own, no new E line.

  python usb_obd_pipe_bench.py --dut localhost:18081 --tcp localhost:18333
         [--com auto]
The tunnel is the caller's (ssh -N -L 18081:<unit>:80 -L 18333:<unit>:3333
rpi001). Needs pyserial and the unit's USB-C cabled to this PC (the bench
preflight says `connector -> PC (CH342 ...)`); SKIPs without that port.
Only the usb_obd port is opened, never the console port beside it.
"""
import argparse
import os
import socket
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dp_bench import Checks, Dut, Staged, moved  # noqa: E402

BAUD = 2000000
PROBE = "/data/usb_obd_pipe_probe.bin"
ck = Checks("USB OBD PIPE")


def open_com(port):
    import serial
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = BAUD
    ser.timeout = 0.05
    ser.dtr = False            # no line of the adapter is moved by the open
    ser.rts = False
    ser.open()
    ser.reset_input_buffer()
    return ser


def read_until(read, token, secs):
    """read() until token is in what came; the time it took, or None"""
    t0 = time.perf_counter()
    buf = b""
    while time.perf_counter() - t0 < secs:
        d = read()
        if d:
            buf += d
            if token in buf:
                return time.perf_counter() - t0
    return None


def leg_latency(ser, tcp):
    print("== L: a short write, each way", flush=True)
    tcp.settimeout(0.05)

    def tcp_read():
        try:
            return tcp.recv(4096)
        except socket.timeout:
            return b""

    up, down = [], []
    for i in range(40):
        tok = b"PING%04d\r" % i
        ser.write(tok)
        ser.flush()
        dt = read_until(tcp_read, tok, 2.0)
        if dt is not None:
            up.append(dt * 1000.0)
        time.sleep(0.08)
        tok = b"PONG%04d\r" % i
        tcp.sendall(tok)
        dt = read_until(lambda: ser.read(256), tok, 2.0)
        if dt is not None:
            down.append(dt * 1000.0)
        time.sleep(0.08)
    detail = {"com_to_tcp_ms": up and {"n": len(up), "min": round(min(up), 1),
                                       "median": round(statistics.median(up), 1), "max": round(max(up), 1)},
              "tcp_to_com_ms": down and {"n": len(down), "min": round(min(down), 1),
                                         "median": round(statistics.median(down), 1), "max": round(max(down), 1)}}
    ck.check("L1 a short write from the PC is on its way at once (the fastest of 40 under 20 ms)",
             len(up) >= 38 and len(down) >= 38 and min(up) < 20.0, detail)


class Collector(threading.Thread):
    """the TCP side: the numbered lines that arrive"""

    def __init__(self, tcp):
        super().__init__(daemon=True)
        self.tcp = tcp
        self.halt = threading.Event()
        self.nums = []
        self.bytes = 0
        self.start()

    def run(self):
        buf = b""
        self.tcp.settimeout(0.2)
        while not self.halt.is_set():
            try:
                d = self.tcp.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            self.bytes += len(d)
            buf += d
            parts = buf.split(b"\n")
            buf = parts.pop()
            for p in parts:
                if len(p) == 7 and p.isdigit():
                    self.nums.append(int(p))

    def close(self):
        self.halt.set()
        self.join(timeout=2)


def stream(ser, first, n_lines, rate):
    """n_lines numbered lines of 8 bytes at `rate` bytes a second, 400 bytes at a time"""
    per = 50
    t0 = time.perf_counter()
    for k in range(0, n_lines, per):
        ser.write(b"".join(b"%07d\n" % (first + i) for i in range(k, min(k + per, n_lines))))
        wait = t0 + (k + per) * 8.0 / rate - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    ser.flush()


def path_losses(dut):
    """the loss counters of this run's path: the bridge's two ends and the server"""
    return {k: v for k, v in dut.losses().items() if "br_usb_pipe" in k or "[slcan0]" in k}


def flash_ops(dut):
    """how often the unit wrote and erased its flash since boot"""
    f = ((dut.get("/api/status") or {}).get("health") or {}).get("flash") or {}
    return (f.get("writes", 0), f.get("erases", 0))


def leg_volume(dut, ser, tcp):
    """The stream with the unit not writing its flash. The unit writes it on
    its own in the first half minute after a boot (the configuration copies
    are refreshed 20 s after a boot that follows a settings change, as this
    run's is), and what the UART loses then is leg F's subject: on
    2026-11-18 this leg began 20 s after the boot and lost 5 lines of 7,500
    to that write, twice the same. So a pass during which the flash counters
    moved is made again."""
    print("== V: 60,000 bytes at 50,000 bytes a second", flush=True)
    n = 7500
    wrote = []
    dut.settled(30)
    for k in range(4):
        first = k * 10000
        f0 = flash_ops(dut)
        l0 = path_losses(dut)
        col = Collector(tcp)
        stream(ser, first, n, 50000)
        time.sleep(1.5)
        col.close()
        mv = moved(l0, path_losses(dut))
        f1 = flash_ops(dut)
        clean = col.nums == list(range(first, first + n)) and not mv
        if clean or f1 == f0:
            break
        wrote.append({"writes": f1[0] - f0[0], "erases": f1[1] - f0[1],
                      "lines_lost": n - len(set(col.nums) & set(range(first, first + n)))})
        print("   lines lost while the unit wrote its flash (%s): once more" % wrote[-1], flush=True)
        time.sleep(2)
    ck.check("V1 every line arrives once and in order, no loss counter moved",
             clean,
             {"sent": n, "arrived": len(col.nums), "in_order": col.nums == sorted(col.nums), "moved": mv,
              "passes_lost_to_a_flash_write_of_the_unit": wrote})


def leg_flash(a, dut, ser, tcp):
    print("== F: the same stream while the unit writes its flash", flush=True)
    n = 37500                       # 6 s at 50,000 bytes a second
    f0 = ((dut.get("/api/status") or {}).get("health") or {}).get("flash") or {}
    l0 = path_losses(dut)
    col = Collector(tcp)
    done = []

    def writer():
        time.sleep(1.0)
        for _ in range(3):
            try:
                req = urllib.request.Request(dut.base + "/api/fs/upload?path=" + PROBE, data=os.urandom(32768),
                                             method="POST", headers={"Content-Type": "application/octet-stream"})
                urllib.request.urlopen(req, timeout=20).read()
                done.append(1)
            except Exception as e:
                print("   upload: %s" % e, flush=True)
            time.sleep(0.5)

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    stream(ser, 100000, n, 50000)
    th.join(timeout=30)
    time.sleep(1.5)
    col.close()
    try:
        urllib.request.urlopen(urllib.request.Request(dut.base + "/api/fs/file?path=" + PROBE, method="DELETE"),
                               timeout=10).read()
    except Exception:
        pass
    f1 = ((dut.get("/api/status") or {}).get("health") or {}).get("flash") or {}
    mv = moved(l0, path_losses(dut))
    seen = set(col.nums)
    lost = n - len([i for i in range(100000, 100000 + n) if i in seen])
    ck.check("F1 the flash was written during the stream and lines were lost",
             len(done) == 3 and f1.get("erases", 0) > f0.get("erases", 0) and lost > 0,
             {"uploads": len(done), "erases": (f0.get("erases"), f1.get("erases")), "lines_lost": lost,
              "of": n})
    ck.check("F2 the loss is in a counter of a route", lost == 0 or bool(mv), {"lines_lost": lost, "moved": mv})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--tcp", default="localhost:18333", help="the unit's port 3333 through the tunnel")
    ap.add_argument("--com", default="auto", help="the usb_obd COM port (auto = the CH342's channel B)")
    a = ap.parse_args()

    port = a.com
    if port in ("", "auto"):
        import detect_ports
        hit = detect_ports.detect(probe=False)["roles"].get("ch342_obd")
        port = hit["port"] if hit else None
    if not port:
        ck.skip("the usb_obd legs", "no CH342 channel B: the unit's USB-C is not cabled to this PC")
        return ck.finish()

    dut = Dut(a.dut)
    h = dut.health(wait_s=40)
    if h["version"] is None:
        print("USB OBD PIPE FAIL: no unit at", a.dut)
        return 1
    print("unit: %s boot_count=%s unexpected=%s log_errors=%s; usb_obd on %s" % (
        h["version"], h["boot_count"], h["unexpected_resets"], h["log_errors"], port), flush=True)
    st = Staged(dut, ("bridge_manager", "socket_manager"), "USB OBD PIPE")
    bm = st.copy("bridge_manager")
    for b in bm["bridges"]:
        if "usb_obd" in (b.get("a"), b.get("b")) or "slcan0" in (b.get("a"), b.get("b")):
            b["enabled"] = False
    bm["bridges"] = [b for b in bm["bridges"] if b.get("name") != "br_usb_pipe"]
    bm["bridges"].append({"name": "br_usb_pipe", "a": "usb_obd", "b": "slcan0", "translator": "raw",
                          "enabled": True})
    sock = st.copy("socket_manager")
    for s in sock["servers"]:
        if s["name"] == "slcan0":
            s.update({"enabled": True, "proto": "tcp", "port": 3333})
    st.submit({"bridge_manager": bm, "socket_manager": sock}, "usb_obd <-raw-> slcan0")
    ser = tcp = None
    try:
        row = next((b for b in (dut.get("/api/bridges") or {}).get("bridges", []) if b["name"] == "br_usb_pipe"), {})
        if ck.check("the pipe is up", row.get("up") is True, row.get("reason", "")):
            ser = open_com(port)
            host, tport = a.tcp.split(":")[0], int(a.tcp.split(":")[1])
            tcp = socket.create_connection((host, tport), timeout=5)
            tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            time.sleep(0.5)
            leg_latency(ser, tcp)
            leg_volume(dut, ser, tcp)
            leg_flash(a, dut, ser, tcp)
    except Exception as e:
        ck.check("the legs ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        for c in (ser, tcp):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    print("== R: restore", flush=True)
    st.restore(ck)
    return ck.finish()


if __name__ == "__main__":
    sys.exit(main())
