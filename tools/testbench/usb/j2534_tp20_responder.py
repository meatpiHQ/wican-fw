#!/usr/bin/env python3
"""The ECU side of a TP 2.0-shaped block transfer, played by PCAN (64-bit
python-can). Pairs with j2534_tp20_tester.py (the tool side, through the DLL).

Convention (the transport shape only, no diagnostic content):
  tester -> 0x740 [0x1N 00 02 1A bb]  "send me block bb" (N = its sequence)
  ECU    -> 0x300 37 frames of 7 payload bytes = 255 bytes + the length word:
            opcode 0x2N (more follow, no ACK) for ordinary frames,
            0x0N (block end, wait for the tester's ACK, more follow) every
            BS-th frame, 0x1N (last frame, wait for ACK) on the final one;
            after a 0x0N / 0x1N frame the ECU waits for the tester's ACK
            0xB(N+1) on 0x740 before sending more (TP 2.0 T1, here 2 s).
  tester -> 0x740 [0xA8]             done: the responder exits.
Every 0x0N / 0x1N frame's ACK latency (ECU frame out -> tester ACK in) is
recorded: that is the round trip the tool imposes on the ECU at every
block end. T3 = the gap the ECU keeps between its frames.

usage: python j2534_tp20_responder.py [--pcan PCAN_USBBUS2] [--bs 15] [--t3-ms 1]
       [--blocks 40] [--timeout 120]
Prints RESPONDER DONE with the statistics, or RESPONDER FAIL.
"""
import argparse
import statistics
import sys
import threading
import time

import can

REQ_ID, DATA_ID = 0x740, 0x300


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcan", default="PCAN_USBBUS2")
    ap.add_argument("--bs", type=int, default=15)
    ap.add_argument("--t3-ms", type=float, default=1.0)
    ap.add_argument("--blocks", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--ack-timeout", type=float, default=2.0)
    a = ap.parse_args()

    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000, receive_own_messages=False)
    while bus.recv(timeout=0):
        pass

    inbox = []
    lock = threading.Condition()
    halt = threading.Event()

    def reader():
        while not halt.is_set():
            m = bus.recv(timeout=0.1)
            if m is None or m.is_error_frame or m.arbitration_id != REQ_ID:
                continue
            with lock:
                inbox.append((time.perf_counter(), bytes(m.data)))
                lock.notify_all()

    threading.Thread(target=reader, daemon=True).start()

    def wait_for(pred, timeout):
        end = time.perf_counter() + timeout
        with lock:
            while True:
                for i, (ts, d) in enumerate(inbox):
                    if pred(d):
                        del inbox[:i + 1]
                        return ts, d
                left = end - time.perf_counter()
                if left <= 0:
                    return None, None
                lock.wait(left)

    def send(data):
        bus.send(can.Message(arbitration_id=DATA_ID, is_extended_id=False, data=data), timeout=1.0)

    ack_lat, block_times = [], []
    served, fails = 0, []
    t_start = time.perf_counter()
    print("responder: waiting for the tester (bs %d, t3 %.1f ms, %d blocks)" % (a.bs, a.t3_ms, a.blocks), flush=True)

    while served < a.blocks and time.perf_counter() - t_start < a.timeout:
        ts, req = wait_for(lambda d: (len(d) >= 5 and (d[0] >> 4) == 1 and d[3] == 0x1A) or d[:1] == b"\xA8", 5.0)
        if req is None:
            continue
        if req[:1] == b"\xA8":
            print("responder: tester disconnected", flush=True)
            break
        blk = req[4]
        t0 = time.perf_counter()
        payload = bytes([0x00, 0xFF]) + bytes(((blk * 7 + i) & 0xFF) for i in range(255))   # length word + 255 bytes
        frames = [payload[i:i + 7] for i in range(0, len(payload), 7)]
        seq = 0
        ok = True
        for i, chunk in enumerate(frames):
            last = i == len(frames) - 1
            block_end = (i + 1) % a.bs == 0
            op = 0x10 if last else (0x00 if block_end else 0x20)
            send(bytes([op | seq]) + chunk)
            t_sent = time.perf_counter()
            expect = (seq + 1) & 0xF
            seq = expect
            if last or block_end:
                ts_ack, ack = wait_for(lambda d, e=expect: len(d) >= 1 and d[0] == (0xB0 | e), a.ack_timeout)
                if ack is None:
                    fails.append("block %d frame %d: no ACK B%X within %.1f s" % (blk, i, expect, a.ack_timeout))
                    ok = False
                    break
                ack_lat.append(ts_ack - t_sent)
            else:
                time.sleep(a.t3_ms / 1000.0)
        if ok:
            served += 1
            block_times.append(time.perf_counter() - t0)

    halt.set()
    time.sleep(0.2)
    bus.shutdown()

    if ack_lat:
        ack_lat.sort()
        print("ACK latency at block ends (n=%d): median %.1f ms, p95 %.1f ms, max %.1f ms" % (
            len(ack_lat), statistics.median(ack_lat) * 1000, ack_lat[int(len(ack_lat) * 0.95)] * 1000, ack_lat[-1] * 1000), flush=True)
    if block_times:
        print("per block (255 B, %d frames, %d ACK waits): median %.1f ms, max %.1f ms" % (
            len(frames), (len(frames) + a.bs - 1) // a.bs, statistics.median(block_times) * 1000, max(block_times) * 1000), flush=True)
    if fails or served < a.blocks:
        print("RESPONDER FAIL: served %d of %d; %s" % (served, a.blocks, "; ".join(fails[:5])), flush=True)
        sys.exit(1)
    print("RESPONDER DONE: %d blocks" % served, flush=True)


if __name__ == "__main__":
    main()
