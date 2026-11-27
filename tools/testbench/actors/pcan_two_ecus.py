#!/usr/bin/env python3
"""Two ECUs that both answer one request, on the PCAN.

A row that sets no request header of its own goes out on the functional id
(7DF, or 18DB33F1 on a 29-bit protocol), and every ECU that knows the
identifier answers. This actor plays two of them for one
ReadDataByIdentifier request, each with its own reply id, its own data and
its place in the order, so a bench can say which answer the device reads
(`obd/autopid_two_ecus_bench.py`).

  A  --a-id (7EC)  answers 62 <did> <--a-data>
  B  --b-id (7EA)  answers 62 <did> <--b-data>, or refuses with
                   `7F 22 <nn>` when --b-data is `nrc:<nn>`
  The second answer follows the first by --gap-ms (15). A answers first;
  --b-first turns the order round. --b-mode always | never.

Both answer on the request id --req (7DF; 0x18DB33F1 for 29-bit ids, with
reply ids like 0x18DAF1DA). Single frames only (up to four data bytes
behind the identifier). Everything else on the bus is left alone.

usage: pcan_two_ecus.py [secs] [--pcan PCAN_USBBUS1] [--did 2006]
          [--req 0x7DF] [--a-id 0x7EC] [--a-data 01E240]
          [--b-id 0x7EA] [--b-data FFFFFF] [--gap-ms 15] [--b-first]
          [--b-mode always] [--pad AA]
Prints `READY`, then one line per request (`REQ`) and per answer (`RSP`)
with the time since the start, and `SUMMARY requests N a N b N` at the end
of the run.
"""
import argparse
import time

import can


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("secs", nargs="?", type=float, default=120)
    ap.add_argument("--pcan", default="PCAN_USBBUS1")
    ap.add_argument("--did", default="2006")
    ap.add_argument("--req", default="0x7DF")
    ap.add_argument("--a-id", default="0x7EC")
    ap.add_argument("--a-data", default="01E240")
    ap.add_argument("--b-id", default="0x7EA")
    ap.add_argument("--b-data", default="FFFFFF")
    ap.add_argument("--gap-ms", type=float, default=15.0)
    ap.add_argument("--b-first", action="store_true")
    ap.add_argument("--b-mode", default="always", choices=("always", "never"))
    ap.add_argument("--pad", default="AA")
    a = ap.parse_args()

    did = bytes.fromhex(a.did)
    want = bytes([0x22]) + did
    req_id = int(a.req, 0)
    pad = int(a.pad, 16)
    ecus = {"a": (int(a.a_id, 0), a.a_data), "b": (int(a.b_id, 0), a.b_data)}
    count = {"req": 0, "a": 0, "b": 0}
    bus = can.Bus(interface="pcan", channel=a.pcan, bitrate=500000)
    t0 = time.time()

    def answer(who):
        arb, data = ecus[who]
        if data.lower().startswith("nrc:"):
            payload = bytes([0x7F, 0x22, int(data[4:], 16)])
        else:
            payload = bytes([0x62]) + did + bytes.fromhex(data)
        frame = bytes([len(payload)]) + payload
        frame = frame + bytes([pad]) * (8 - len(frame))
        bus.send(can.Message(arbitration_id=arb, is_extended_id=arb > 0x7FF,
                             data=frame), timeout=0.2)
        count[who] += 1
        print("RSP %8.3f %X %s" % (time.time() - t0, arb,
                                   frame.hex(" ").upper()), flush=True)

    order = ("b", "a") if a.b_first else ("a", "b")
    print("TWO ECUS on %s: 22%s on %X -> A %X %s, B %X %s (%s first, the "
          "other +%.0f ms, B %s)"
          % (a.pcan, a.did.upper(), req_id, ecus["a"][0], a.a_data,
             ecus["b"][0], a.b_data, order[0].upper(), a.gap_ms, a.b_mode),
          flush=True)
    print("READY", flush=True)
    end = t0 + a.secs
    try:
        while time.time() < end:
            m = bus.recv(timeout=0.2)
            if m is None or m.is_error_frame or m.arbitration_id != req_id:
                continue
            d = bytes(m.data)
            if len(d) < 4 or d[0] != 0x03 or d[1:4] != want:
                continue
            count["req"] += 1
            print("REQ %8.3f %X %s" % (time.time() - t0, m.arbitration_id,
                                       d.hex(" ").upper()), flush=True)
            sent = 0
            for who in order:
                if who == "b" and a.b_mode == "never":
                    continue
                if sent and a.gap_ms > 0:
                    time.sleep(a.gap_ms / 1000.0)
                answer(who)
                sent += 1
    finally:
        bus.shutdown()
    print("SUMMARY requests %d a %d b %d" % (count["req"], count["a"],
                                             count["b"]), flush=True)


if __name__ == "__main__":
    main()
