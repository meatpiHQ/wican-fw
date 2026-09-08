#!/usr/bin/env python3
"""ATMA throughput / loss bench: streams a CAN flood through the MIC chip's
monitor mode to an ELM app on TCP:35000 and places every lost frame at a
hop: chip -> UART ring -> obd fan-out -> bridge -> socket -> client.

Frame sources (pick one):
  --source pcan   (PC, python-can + PCAN on the DUT bus) sends --count
                  frames on --id at --rate frames/s, data bytes 0..3 = a
                  big-endian counter, bytes 4..7 = 0xA5 pattern. Exactly-N
                  accounting: the client must see every counter once, in
                  order (the §7 conservation rule - a deterministic delta
                  is a finding, never noise).
  --source sim    the WiCAN ECU Simulator's `broadcast_enabled` flood
                  (reboot-to-apply, ~20 s each way; ONE id at kHz rate, the
                  id differs between the simulator's boots). No counter, so
                  only rate and line integrity are judged. Restored to the
                  value found.

The capture runs ON rpi001 (`atma_capture.py`, the app's seat over the
hotspot); counters are read here through the DUT API before and after:
  /api/obd_chip  uart.rx_bytes (what the chip printed), rx_overflows,
                 subscribers[].dropped (fan-out drops per bridge queue)
  /api/bridges   br_tcp_obd b2a_bytes (pumped toward the socket)
  /api/sockets   obd0 bytes_out / tx_drops

  python atma_flood_bench.py [dut host[:port]] [--dut-ip 10.42.1.194]
        --source pcan [--pcan PCAN_USBBUS1] [--rate 1000] [--count 5000] [--id 0x123]
        --source sim  [--sim 192.168.8.1] [--secs 10]
Expected final line: ATMA FLOOD PASS
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import benchlib as bl  # noqa: E402

DUT = "localhost:8081"
DUT_IP = "10.42.1.194"
SIM = "192.168.8.1"
SOURCE = "sim"
PCAN = "PCAN_USBBUS1"
RATE = 1000
COUNT = 5000
CAN_ID = 0x123
SECS = 10.0
SERIAL = ""     # "COM39:1000000" = a USB ELM adapter on this PC instead of
                # the WiCAN: the capture runs locally, no DUT counters
CAPTURE = os.path.join(os.path.dirname(__file__), "atma_capture.py")

args = sys.argv[1:]
i = 0
while i < len(args):
    k = args[i]
    if k == "--dut-ip":
        DUT_IP = args[i + 1]; i += 2
    elif k == "--sim":
        SIM = args[i + 1]; i += 2
    elif k == "--source":
        SOURCE = args[i + 1]; i += 2
    elif k == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif k == "--rate":
        RATE = int(args[i + 1]); i += 2
    elif k == "--count":
        COUNT = int(args[i + 1]); i += 2
    elif k == "--id":
        CAN_ID = int(args[i + 1], 16); i += 2
    elif k == "--secs":
        SECS = float(args[i + 1]); i += 2
    elif k == "--serial":
        SERIAL = args[i + 1]; i += 2
    else:
        DUT = k; i += 1

fails: list[str] = []
metrics: list[tuple[str, object, str]] = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def metric(name, value, unit=""):
    metrics.append((name, value, unit))
    print(f"METRIC {name}={value}{(' ' + unit) if unit else ''}")


def api(host, path, method="GET", body=None):
    st, text = bl.api(host, path, method=method, body=body, classify=False)
    try:
        return st, json.loads(text)
    except Exception:
        return st, text


def counters():
    _, chip = api(DUT, "/api/obd_chip")
    _, br = api(DUT, "/api/bridges")
    _, sk = api(DUT, "/api/sockets")
    st, can = api(DUT, "/api/can")
    tcp = next((b for b in br.get("bridges", []) if b["name"] == "br_tcp_obd"),
               {}).get("stats", {})
    obd0 = next((s for s in sk.get("servers", []) if s["name"] == "obd0"),
                {}).get("stats", {})
    subs = chip.get("subscribers", [])   # several are named "bridge"
    return {
        "chip_rx": chip.get("uart", {}).get("rx_bytes", 0),
        "chip_ovf": chip.get("uart", {}).get("rx_overflows", 0),
        "chip_tx": chip.get("uart", {}).get("tx_bytes", 0),
        "fanout_dropped": sum(s.get("dropped", 0) for s in subs),
        "subs": [f"{s.get('name')}#{s.get('idx', i)}={s.get('dropped', 0)}"
                 for i, s in enumerate(subs)],
        "bridge_b2a": tcp.get("b2a_bytes", 0),
        "bridge_send_err": tcp.get("send_errors", 0),
        "sock_out": obd0.get("bytes_out", 0),
        "sock_tx_drops": obd0.get("tx_drops", 0),
        # the native TWAI controller shares the bus: its rx counter is the
        # on-bus witness that separates chip-side loss from bus-side loss
        "can_rx": can.get("rx", 0) if isinstance(can, dict) and
        can.get("running") else None,
        "can_rx_missed": can.get("rx_missed", 0) if isinstance(can, dict)
        else 0,
    }


def delta(a, b):
    return {k: (b[k] - a[k]) for k in a
            if isinstance(a[k], int) and isinstance(b[k], int)}


# ---- frame sources -----------------------------------------------------------
def sim_get_broadcast():
    st, cfg = api(SIM, "/api/settings/ecu_sim")
    return cfg if st == 200 else None


def sim_set_broadcast(cfg, on):
    body = dict(cfg)
    for k in ("degraded", "pending_reboot"):
        body.pop(k, None)
    body["broadcast_enabled"] = on
    api(SIM, "/api/settings/ecu_sim", "PUT", body)
    st, r = api(SIM, "/api/settings/submit", "POST")
    print(f"   sim broadcast_enabled={on}: submit -> {st} {r}")
    if isinstance(r, dict) and r.get("reboot"):
        time.sleep(22)
        for _ in range(20):
            st, _ = api(SIM, "/api/info")
            if st == 200:
                break
            time.sleep(2)


class PcanSender(threading.Thread):
    """Sends COUNT counter frames at RATE/s; records sent count + errors."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sent = 0
        self.errors = 0
        self.elapsed = 0.0

    def run(self):
        import can  # python-can + PCAN drivers
        bus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
        period = 1.0 / RATE
        t0 = time.perf_counter()
        for n in range(COUNT):
            data = n.to_bytes(4, "big") + bytes([0xA5, 0x5A, 0xA5, 0x5A])
            try:
                bus.send(can.Message(arbitration_id=CAN_ID, data=data,
                                     is_extended_id=False), timeout=1.0)
                self.sent += 1
            except can.CanError:
                self.errors += 1
            target = t0 + (n + 1) * period
            while time.perf_counter() < target:
                pass
        self.elapsed = time.perf_counter() - t0
        bus.shutdown()


def main():
    if SERIAL:
        port, _, baud = SERIAL.partition(":")
        print(f"ATMA flood bench: source={SOURCE}, USB adapter on {port} "
              f"@ {baud or 115200} (no DUT counters)")
    else:
        print(f"ATMA flood bench: source={SOURCE} DUT api {DUT}, TCP "
              f"{DUT_IP}:35000")
        st, info = api(DUT, "/api/info")
        check("DUT answers /api/info", st == 200)
        st, chip = api(DUT, "/api/obd_chip")
        check("/api/obd_chip served (fan-out counters)", st == 200 and
              "uart" in chip, str(chip)[:80])
        check("chip ready", chip.get("ready") is True)
        rc, out = bl.sh(f'scp -q "{CAPTURE}" rpi001:/tmp/atma_capture.py')
        check("capture script on the Pi", rc == 0, out[-100:])

    sim_cfg = None
    if SOURCE == "sim":
        sim_cfg = sim_get_broadcast()
        check("simulator reachable", sim_cfg is not None)
        if sim_cfg and not sim_cfg.get("broadcast_enabled"):
            sim_set_broadcast(sim_cfg, True)
        secs = SECS
        seq_arg = ""
    else:
        secs = COUNT / RATE + 3.0
        seq_arg = f" --seq {CAN_ID:03X}"

    before = counters() if not SERIAL else {}
    if before:
        print("   before: " + json.dumps(before))

    if SERIAL:
        port, _, baud = SERIAL.partition(":")
        cmd = [sys.executable, CAPTURE, "--serial", port, "--baud",
               baud or "115200", "--secs", str(secs)]
        if seq_arg:
            cmd += ["--seq", f"{CAN_ID:03X}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    else:
        proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o",
             f"ConnectTimeout={bl.SSH_CONNECT}", "rpi001",
             f"python3 /tmp/atma_capture.py {DUT_IP} --secs {secs}"
             f"{seq_arg} --raw /tmp/atma_raw.bin"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sender = None
    if SOURCE == "pcan":
        time.sleep(2.5)  # capture init + ATMA armed
        sender = PcanSender()
        sender.start()
        sender.join()
    out = proc.communicate()[0]
    res = {}
    for line in out.splitlines():
        if line.startswith("RESULT "):
            res = json.loads(line[len("RESULT "):])
        else:
            print("   pi: " + line)
    time.sleep(1.0)
    after = counters() if not SERIAL else {}
    d = delta(before, after) if not SERIAL else {}
    print("   capture: " + json.dumps({k: v for k, v in res.items()
                                       if k != "init"}))
    print("   init: " + json.dumps(res.get("init", {})))
    if d:
        print("   delta:  " + json.dumps(d))

    check("monitor stream captured", res.get("lines", 0) > 0,
          f"{res.get('lines')} lines in {res.get('secs')} s")
    check("prompt returned after the stop byte", res.get("prompt_back") is True)
    check("no malformed lines (no spliced/truncated chunks)",
          res.get("malformed", 1) == 0,
          f"{res.get('malformed')} e.g. {res.get('malformed_first')}")
    client = res.get("bytes", 0)
    chip_rx = d.get("chip_rx", 0)
    if not SERIAL:
        check("no UART overflow on the chip link", d.get("chip_ovf", 1) == 0,
              f"+{d.get('chip_ovf')}")
        check("no obd fan-out drops", d.get("fanout_dropped", 1) == 0,
              f"+{d.get('fanout_dropped')} {after['subs']}")
        check("no socket tx drops / bridge send errors",
              d.get("sock_tx_drops", 1) == 0 and
              d.get("bridge_send_err", 1) == 0,
              f"tx_drops +{d.get('sock_tx_drops')} send_err "
              f"+{d.get('bridge_send_err')}")
        # byte conservation across the hops (the chip's own echo/prompt
        # bytes are a few dozen; allow 2 KB slack for the init exchange)
        sock_out = d.get("sock_out", 0)
        check("bytes conserved chip -> socket -> client",
              chip_rx > 0 and abs(chip_rx - sock_out) <= 2048 and
              abs(sock_out - client) <= 2048,
              f"chip {chip_rx} B, socket out {sock_out} B, client {client} B")
        metric("chip_rx_bytes", chip_rx, "B")
    metric("lines_per_s", res.get("lines_per_s"), "lines/s")
    metric("bytes_per_s", res.get("bytes_per_s"), "B/s")
    metric("client_bytes", client, "B")

    if SOURCE == "pcan" and sender is not None:
        metric("sent", sender.sent)
        metric("send_errors", sender.errors)
        metric("send_rate", round(sender.sent / sender.elapsed, 1)
               if sender.elapsed else 0, "frames/s")
        check("every counter frame seen once, in order (exactly-N)",
              res.get("seq_count") == sender.sent and
              res.get("seq_missing") == 0 and res.get("seq_dupes") == 0 and
              res.get("seq_out_of_order") == 0,
              f"sent {sender.sent}, seen {res.get('seq_count')}, missing "
              f"{res.get('seq_missing')} gaps {res.get('seq_gaps')}, dupes "
              f"{res.get('seq_dupes')}, ooo {res.get('seq_out_of_order')}")
        # attribution: the native TWAI on the same bus counts every frame
        # it saw; a frame the witness has and the client lacks was lost in
        # the chip (or its UART link), a frame neither saw never made it
        # onto the bus (sender/arbitration side)
        if not SERIAL and before.get("can_rx") is not None and "can_rx" in d:
            witness = d["can_rx"]
            metric("can_witness_rx", witness, "frames")
            lost_client = sender.sent - (res.get("seq_count") or 0)
            check("loss attribution: the on-bus witness (native CAN) saw "
                  "every frame the sender put out",
                  witness >= sender.sent,
                  f"witness +{witness} (rx_missed +{d.get('can_rx_missed')}) "
                  f"vs sent {sender.sent}; client short by {lost_client} -> "
                  + ("chip/UART-side loss" if witness >= sender.sent and
                     lost_client > 0 else "bus/sender-side loss"
                     if lost_client > 0 else "no loss"))
        elif not SERIAL:
            print("   (no native-CAN witness: can_manager not running)")
    else:
        ids = res.get("ids", {})
        metric("dominant_id", next(iter(ids), None))
        print(f"   ids: {ids}")

    if SOURCE == "sim" and sim_cfg is not None and \
            not sim_cfg.get("broadcast_enabled"):
        sim_set_broadcast(sim_cfg, False)
        print("   simulator broadcast restored to off (as found)")

    print()
    for name, value, unit in metrics:
        print(f"  {name:>16} = {value} {unit}")
    if fails:
        print(f"\nATMA FLOOD FAIL ({len(fails)}): " + "; ".join(fails))
        return 1
    print("\nATMA FLOOD PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
