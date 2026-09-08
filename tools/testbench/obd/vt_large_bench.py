#!/usr/bin/env python3
"""Large-payload OBD bench: the MIC chip's 4 KB ISO-TP paths end to end
through the product's app path (TCP:35000 -> bridge -> UART -> chip -> CAN)
against the ECU simulator's synthetic memory (byte at A = A & 0xFF;
0x23 ReadMemoryByAddress serves up to 4094 bytes, 0x3D WriteMemoryByAddress
verifies the bytes it receives).

Legs (the chip-facing loop runs ON rpi001 = `vt_large_capture.py`, the
app's seat over the hotspot; counters are read here through the DUT API):
  tx   VTFullyRequestCk with 64 / 512 / 2048 / 4064 pattern bytes: the
       8 KB ASCII command crosses socket -> bridge -> UART intact and the
       simulator's positive 7D proves every byte of the multi-frame
       transfer (ms per size = the chip's transmit time)
  rx   `23 23 <addr> <size>` for 64 / 512 / 2048 / 4094 bytes: the chip
       receives the multi-frame response and prints ~8-13 KB of ASCII;
       byte-exact at the client, no UART overflow, no fan-out drops, no
       socket drops (/api/obd_chip, /api/bridges, /api/sockets deltas)
  eng  the same 4094-byte read through the COMMAND ENGINE
       (POST /api/autopid/test = obd_chip_request, the path autopid and the
       UDS manager use): reported as a finding when the raw text is cut at
       the engine's accumulator size

  python vt_large_bench.py [dut host[:port]] [--dut-ip 10.42.1.194]
                           [--tx 64,512,2048,4064] [--rx 64,512,2048,4094]
Expected final line: VT LARGE PASS
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import benchlib as bl  # noqa: E402

DUT = "localhost:8081"
DUT_IP = "10.42.1.194"
TX = "64,512,2048,4064"
RX = "64,512,2048,4094"
ECU = "sim"            # sim = the simulator's 7E0/7E8 memory service;
PCAN = "PCAN_USBBUS2"  # pcan = pcan_memory_ecu.py on 7E4/7EC (this PC)
TXID, RXID = "7E0", "7E8"
CAPTURE_ARGS = ""   # extra vt_large_capture.py flags: "--svc 3D --spaces"
SERIAL = ""         # "COM39:1000000" = a USB ELM adapter on this PC instead
                    # of the WiCAN: capture runs locally, no DUT API/counters
CAPTURE = os.path.join(os.path.dirname(__file__), "vt_large_capture.py")
PCAN_ECU = os.path.join(os.path.dirname(__file__), "pcan_memory_ecu.py")
VENV_PY = r"C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe"

args = sys.argv[1:]
i = 0
while i < len(args):
    k = args[i]
    if k == "--dut-ip":
        DUT_IP = args[i + 1]; i += 2
    elif k == "--tx":
        TX = args[i + 1]; i += 2
    elif k == "--rx":
        RX = args[i + 1]; i += 2
    elif k == "--ecu":
        ECU = args[i + 1]; i += 2
    elif k == "--pcan":
        PCAN = args[i + 1]; i += 2
    elif k == "--capture-args":
        CAPTURE_ARGS = args[i + 1]; i += 2
    elif k == "--serial":
        SERIAL = args[i + 1]; i += 2
    else:
        DUT = k; i += 1
SIM = "192.168.8.1"
if ECU == "pcan":
    # The chip only assembles multi-frame responses (and sends flow
    # control) for the OBD physical range 7E0-7E7 -> 7E8-7EF: on 740/748 it
    # printed raw First Frames and never continued, ATFCSH/FCSD/FCSM1 or
    # not (bench 2026-09-08). But the simulator's ISO-TP links answer the
    # whole 7E0-7E7 range too (a 7E4 test got its FC, a second 7D and its
    # 255-byte 23 reply on top of the PCAN ECU's). So: 7E4/7EC with the
    # simulator's ECU switched off for the run (ecu_sim.enabled, its own
    # settings API, one reboot each way), restored afterwards.
    TXID, RXID = "7E4", "7EC"

fails: list[str] = []
warns: list[str] = []
metrics: list[tuple[str, object, str]] = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def warn(name, ok, detail=""):
    print(("PASS" if ok else "WARN") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        warns.append(name)


def metric(name, value, unit=""):
    metrics.append((name, value, unit))
    print(f"METRIC {name}={value}{(' ' + unit) if unit else ''}")


def api(path, method="GET", body=None, host=None):
    st, text = bl.api(host or DUT, path, method=method, body=body,
                      classify=False)
    try:
        return st, json.loads(text)
    except Exception:
        return st, text


def counters():
    _, chip = api("/api/obd_chip")
    _, sk = api("/api/sockets")
    obd0 = next((s for s in sk.get("servers", []) if s["name"] == "obd0"),
                {}).get("stats", {})
    return {
        "chip_rx": chip.get("uart", {}).get("rx_bytes", 0),
        "chip_tx": chip.get("uart", {}).get("tx_bytes", 0),
        "chip_ovf": chip.get("uart", {}).get("rx_overflows", 0),
        "rx_max_chunk": chip.get("uart", {}).get("rx_max_chunk", 0),
        "fanout_dropped": sum(s.get("dropped", 0)
                              for s in chip.get("subscribers", [])),
        "sock_in": obd0.get("bytes_in", 0),
        "sock_out": obd0.get("bytes_out", 0),
        "sock_rx_drops": obd0.get("rx_drops", 0),
        "sock_tx_drops": obd0.get("tx_drops", 0),
    }


def sim_set_enabled(on):
    """Flip the simulator's ECU on/off (reboot-to-apply, ~25 s). Returns
    the previous value, or None when the simulator is unreachable."""
    st, cfg = api("/api/settings/ecu_sim", host=SIM)
    if st != 200 or not isinstance(cfg, dict):
        return None
    prev = cfg.get("enabled")
    if prev == on:
        return prev
    body = {k: v for k, v in cfg.items() if k not in ("degraded",
                                                       "pending_reboot")}
    body["enabled"] = on
    api("/api/settings/ecu_sim", "PUT", body, host=SIM)
    st, r = api("/api/settings/submit", "POST", host=SIM)
    print(f"   simulator ecu_sim.enabled={on}: submit -> {st} {r}")
    time.sleep(22)
    for _ in range(20):
        st, _ = api("/api/info", host=SIM)
        if st == 200:
            break
        time.sleep(2)
    return prev


def main():
    paused = []
    if SERIAL:
        port, _, baud = SERIAL.partition(":")
        print(f"VT large bench: USB adapter on {port} @ {baud or 115200} "
              f"(no DUT API/counters)")
    else:
        print(f"VT large bench: API {DUT}, TCP {DUT_IP}:35000 via rpi001")
        st, info = api("/api/info")
        check("DUT answers /api/info", st == 200)
        st, chip = api("/api/obd_chip")
        check("/api/obd_chip served", st == 200 and "uart" in chip)
        check("chip ready", chip.get("ready") is True)

        # autopid must not poll during the legs (it would interleave with
        # the multi-second transfers); the client-yield rule handles it
        # after the first command, but the first VT line is long - pause
        # the groups.
        st, ap = api("/api/autopid")
        for g in ap.get("groups", []) if isinstance(ap, dict) else []:
            if g.get("enabled"):
                api("/api/autopid/group", "POST", {"name": g["name"],
                                                    "enabled": False})
                paused.append(g["name"])
        if paused:
            print(f"   autopid groups paused: {paused}")
            time.sleep(1.5)

        rc, out = bl.sh(f'scp -q "{CAPTURE}" rpi001:/tmp/vt_large_capture.py')
        check("capture script on the Pi", rc == 0, out[-100:])
        bl.pi("mkdir -p /tmp/vt_raw")

    ecu_proc = None
    sim_prev = None
    if ECU == "pcan":
        sim_prev = sim_set_enabled(False)
        check("simulator ECU switched off for the PCAN legs (7E4/7EC "
              "would collide with it otherwise)", sim_prev is not None,
              f"was enabled={sim_prev}")
        ecu_proc = subprocess.Popen(
            [VENV_PY, PCAN_ECU, "--pcan", PCAN, "--rxid", "0x" + TXID,
             "--txid", "0x" + RXID, "--secs", "240"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(2.0)
        print(f"   PCAN memory ECU up on {PCAN} ({TXID}/{RXID})")

    before = counters() if not SERIAL else {}
    if SERIAL:
        port, _, baud = SERIAL.partition(":")
        proc = subprocess.run(
            [sys.executable, CAPTURE, "--serial", port, "--baud",
             baud or "115200", "--tx", TX, "--rx", RX, "--txid", TXID,
             "--rxid", RXID] + CAPTURE_ARGS.split(),
            capture_output=True, text=True, timeout=600)
    else:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o",
             f"ConnectTimeout={bl.SSH_CONNECT}", "rpi001",
             f"python3 /tmp/vt_large_capture.py {DUT_IP} --tx {TX} "
             f"--rx {RX} --txid {TXID} --rxid {RXID} --raw-dir /tmp/vt_raw "
             f"{CAPTURE_ARGS}"],
            capture_output=True, text=True, timeout=600)
    if ecu_proc is not None:
        ecu_proc.terminate()
        try:
            ecu_out = ecu_proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            ecu_proc.kill()
            ecu_out = ecu_proc.communicate()[0]
        for line in ecu_out.splitlines():
            print("   ecu: " + line[:200])
    results = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.startswith("RESULT "):
            results.append(json.loads(line[len("RESULT "):]))
        else:
            print("   pi: " + line)
    after = counters() if not SERIAL else {}
    d = {k: after[k] - before[k] for k in before}
    if d:
        print("   delta: " + json.dumps(d))
    check("capture finished (DONE)", "DONE" in proc.stdout,
          proc.stderr[-200:])

    for r in [x for x in results if x["leg"] == "tx"]:
        check(f"tx {r['n']} B via VTFullyRequestCk ({r['svc']}): the ECU "
              f"verified every byte (positive response after "
              f"{r['pending']} pending)", r["ok"],
              f"{r['cmd_chars']} chars, {r['ms']} ms, lines {r['lines']}")
        metric(f"tx_{r['n']}_ms", r["ms"], "ms")
    for r in [x for x in results if x["leg"] == "rx"]:
        check(f"rx {r['n']} B: byte-exact at the client", r["exact"],
              f"got {r['got']} of {r['want']}, first_bad={r['first_bad']}, "
              f"{r['chars']} chars / {r['lines']} lines (longest "
              f"{r['line_max_chars']}) in {r['ms']} ms, head {r['head']!r} "
              f"tail {r['tail']!r}")
        metric(f"rx_{r['n']}_ms", r["ms"], "ms")
        if r["ms"]:
            metric(f"rx_{r['n']}_kBps", round(r["chars"] / r["ms"], 1),
                   "kB/s ASCII")
    if not SERIAL:
        check("no UART overflow", d["chip_ovf"] == 0, f"+{d['chip_ovf']}")
        check("no obd fan-out drops", d["fanout_dropped"] == 0,
              f"+{d['fanout_dropped']}")
        check("no socket drops", d["sock_rx_drops"] == 0 and
              d["sock_tx_drops"] == 0,
              f"rx_drops +{d['sock_rx_drops']} tx_drops +{d['sock_tx_drops']}")
        check("bytes conserved socket in -> chip tx (the 8 KB command lines)",
              abs(d["sock_in"] - d["chip_tx"]) <= 256,
              f"socket in {d['sock_in']} B, chip tx {d['chip_tx']} B")
        check("bytes conserved chip rx -> socket out (the 8 KB responses)",
              abs(d["chip_rx"] - d["sock_out"]) <= 256,
              f"chip rx {d['chip_rx']} B, socket out {d['sock_out']} B")
        metric("rx_max_chunk", after["rx_max_chunk"], "B")

        # ---- the command-engine path (autopid / uds_manager) ------------------
        n = int(RX.split(",")[-1])
        cmd = "2323" + format(0x000100, "06X") + format(n, "04X")
        if ECU == "pcan":
            ecu_proc = subprocess.Popen(
                [VENV_PY, PCAN_ECU, "--pcan", PCAN, "--rxid", "0x" + TXID,
                 "--txid", "0x" + RXID, "--secs", "30"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(2.0)
        st, r = api("/api/autopid/test", "POST",
                    {"cmd": cmd,
                     "init": f"ATSH{TXID};ATCRA{RXID};ATCAF1;ATST64"})
        if ECU == "pcan":
            ecu_proc.terminate()
            try:
                ecu_proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                ecu_proc.kill()
        raw = r.get("raw", "") if isinstance(r, dict) else ""
        hexchars = sum(1 for c in raw if c in "0123456789ABCDEF")
        got = hexchars // 2 - 1
        print(f"   engine: HTTP {st}, elapsed "
              f"{r.get('elapsed_ms') if isinstance(r, dict) else '?'} ms, "
              f"raw {len(raw)} chars, ~{got} data bytes decoded, "
              f"ok={r.get('ok') if isinstance(r, dict) else '?'}")
        warn(f"eng {n} B through /api/autopid/test (obd_chip_request) is "
             f"complete", st == 200 and got >= n,
             f"raw {len(raw)} chars -> ~{got} of {n} bytes; the test route's "
             f"own raw buffer is AP_RESP_MAX (1024) chars and the engine "
             f"accumulator is OBD_RESP_MAX (16 KB since 2026-09-08, was 4 KB "
             f"- the console WARN 'exceeded ... bytes (truncated)' is the "
             f"engine's own verdict)")
        metric("eng_raw_chars", len(raw))

    if ECU == "pcan" and sim_prev:
        sim_set_enabled(True)
        print("   simulator ECU restored (enabled)")

    for name in paused:
        api("/api/autopid/group", "POST", {"name": name, "enabled": True})
    if paused:
        print(f"   autopid groups restored: {paused}")

    print()
    for name, value, unit in metrics:
        print(f"  {name:>14} = {value} {unit}")
    if fails:
        print(f"\nVT LARGE FAIL ({len(fails)}): " + "; ".join(fails))
        return 1
    if warns:
        print(f"\nVT LARGE PASS (with {len(warns)} WARN: " + "; ".join(warns) + ")")
        return 0
    print("\nVT LARGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
