#!/usr/bin/env python3
"""Throughput bench for the slcan CAN translator across a transport.

Measures sustained frames/sec each direction through a can<->slcan0 bridge:
  * RX (CAN->slcan->transport): PCAN floods the bus, count slcan lines that
    arrive over the transport socket.
  * TX (transport->slcan->CAN): flood `t...` lines over the socket, count
    frames delivered on PCAN.

Point --host/--port at the transport under test:
  USB-NCM : --host 192.168.82.1 --port 3333      (run on the PC, PCAN local)
  WiFi/STA: --host <dut-wifi-ip> --port 3333      (run where both are reachable)
  WS      : --ws [--ws-path /ws/can]              (needs br can<->ws_can/slcan;
                                                   slcan_ws_test.py sets it up)

  python slcan_perf_test.py --host 192.168.82.1 --secs 5 --pcan PCAN_USBBUS2
Needs python-can. Assumes br_slcan (can<->slcan0/slcan) is configured + the
CAN bus enabled at 500 kbit/s (slcan_bridge_test.py sets this up).
"""
import argparse
import socket
import time


class TcpTransport:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)

    def send(self, data):
        self.s.sendall(data)

    def recv_payloads(self):
        """Non-blocking: whatever bytes are there (socket must be nonblocking)."""
        try:
            return [self.s.recv(65536)]
        except (BlockingIOError, socket.error):
            return []

    def setblocking(self, flag):
        self.s.setblocking(flag)

    def settimeout(self, t):
        self.s.settimeout(t)

    def close(self):
        self.s.close()


class WsTransport:
    def __init__(self, host, path, port=80):
        from wsmin import WS
        self.ws = WS(host, port, path)

    def send(self, data):
        self.ws.send(data)

    def recv_payloads(self):
        return [p for _, p in self.ws.pump()]

    def setblocking(self, flag):
        self.ws.setblocking(flag)

    def settimeout(self, t):
        self.ws.settimeout(t)

    def close(self):
        self.ws.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.82.1")
    ap.add_argument("--port", type=int, default=3333)
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--secs", type=float, default=5.0)
    ap.add_argument("--label", default="USB-NCM")
    ap.add_argument("--ws", action="store_true",
                    help="use the WebSocket transport instead of TCP")
    ap.add_argument("--ws-path", default="/ws/can")
    ap.add_argument("--rx-fps", type=int, default=4600,
                    help="RX flood pacing (just above the 500k/8B bus ceiling "
                         "~4200 — PCAN Write() has no backpressure; unpaced "
                         "floods leave thousands queued in the PEAK driver "
                         "and wedge the adapter)")
    a = ap.parse_args()

    import can

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    if a.ws:
        s = WsTransport(a.host, a.ws_path, a.port if a.port != 3333 else 80)
        where = f"ws://{a.host}{a.ws_path}"
    else:
        s = TcpTransport(a.host, a.port)
        where = f"{a.host}:{a.port}"
    s.send(b"O\r")
    time.sleep(0.3)
    s.setblocking(False)
    s.recv_payloads()  # drain any greeting

    print(f"== slcan throughput over {a.label} ({where}) ==")

    # ---------------- RX: CAN -> slcan -> transport ----------------
    # flood the bus from PCAN; count slcan lines received over the socket.
    msg = can.Message(arbitration_id=0x123, is_extended_id=False,
                      data=[0, 1, 2, 3, 4, 5, 6, 7])
    sent = 0
    got = 0
    buf = b""
    s.setblocking(False)
    t0 = time.time()
    t_end = t0 + a.secs
    while time.time() < t_end:
        # paced flood: keep the bus saturated without over-queueing the driver
        if sent < (time.time() - t0) * a.rx_fps:
            try:
                bus.send(msg, timeout=0)
                sent += 1
            except Exception:
                pass
        for p in s.recv_payloads():
            buf += p
        if buf:
            got += buf.count(b"\r")
            buf = buf[buf.rfind(b"\r") + 1:] if b"\r" in buf else buf
    # drain the tail
    time.sleep(0.3)
    for p in s.recv_payloads():
        got += p.count(b"\r")
    rx_fps = got / a.secs
    print(f"  RX  CAN->slcan : sent {sent} on bus, delivered {got} lines "
          f"= {rx_fps:.0f} fps  (drop {100*(1-got/max(sent,1)):.1f}%)")

    # ---------------- TX: transport -> slcan -> CAN ----------------
    # The RX flood drives the PEAK adapter error-passive (it can't ACK a
    # saturated bus) and a non-ACKing peer then walks the DUT's TWAI to
    # error-passive too — a same-session TX run reads ~0. Re-init the PCAN,
    # then kick the bus with one DUT frame so both nodes re-integrate.
    bus.shutdown()
    time.sleep(0.5)
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    try:
        bus.reset()
    except Exception:
        pass
    s.send(b"t7770F00D\r")  # heal kick: DUT TX, PCAN ACKs + receives
    ok = False
    end = time.time() + 3
    while time.time() < end:
        m = bus.recv(timeout=0.3)
        if m and m.arbitration_id == 0x777:
            ok = True
            break
    print(f"  (bus re-init between legs: heal kick {'ok' if ok else 'FAILED'})")
    while bus.recv(timeout=0):
        pass
    line = b"t1238001122334455667788\r"  # id 0x123, dlc 8
    txsent = 0
    s.setblocking(True)
    s.settimeout(1)
    t_end = time.time() + a.secs
    while time.time() < t_end:
        try:
            s.send(line * 20)  # batch to reduce syscalls / WS frames
            txsent += 20
        except socket.timeout:
            pass
    # count what PCAN received
    rxcount = 0
    t_end = time.time() + 1.0
    while time.time() < t_end:
        if bus.recv(timeout=0.05):
            rxcount += 1
    tx_fps = rxcount / a.secs
    print(f"  TX  slcan->CAN : sent {txsent} lines, {rxcount} on bus "
          f"= {tx_fps:.0f} fps")

    s.close()
    bus.shutdown()
    print(f"  (500 kbit/s 8-byte bus ceiling ~= 4200 fps)")


if __name__ == "__main__":
    main()
