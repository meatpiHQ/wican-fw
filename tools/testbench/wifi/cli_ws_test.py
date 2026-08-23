#!/usr/bin/env python3
"""CLI-over-WebSocket live check (run ON rpi001 against the composed main).

Needs on the DUT (settings): websocket_manager channel `ws_cli` enabled
(text, /ws/cli) + a bridge `br_cli = cli <-raw-> ws_cli`. Drives the
same commands as the UART spot check through ws://<dut>/ws/cli and
verifies every response ends with the legacy `wican> ` prompt.

Expected final line: CLI WS PASS
"""
import sys
import time

import websocket

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
PROMPT = "wican> "


def run_cmd(ws, cmd, timeout=8.0, prompts=1):
    ws.send(cmd + "\n")
    out = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if isinstance(frame, bytes):
            frame = frame.decode(errors="replace")
        out += frame
        if out.endswith(PROMPT) and out.count(PROMPT) >= prompts:
            return out
    raise TimeoutError(f"{cmd!r}: no prompt within {timeout}s; got {out!r}")


def main():
    url = f"ws://{DUT}/ws/cli"
    print(f"connecting {url}")
    ws = websocket.create_connection(url, timeout=1.0)
    failures = 0

    checks = [
        ("version", "device_id:"),
        ("status", "uptime:"),
        ("battery", "battery:"),
        ("rtc", "System time:"),
        ("rtc -r", "(Day "),
        ("imu", "Accel:"),
        ("imu -i", "IMU Device ID:"),
        ("wifi -s", "WiFi Status:"),
        ("sdcard -i", "Capacity:"),
        ("system -m", "RAM Free:"),
        ("system -t", "cpu%"),  # task monitor (1 s CPU% sample window)
        ("restart_tracker -l", "reset="),
        ("help", "restart_tracker"),
        ("bogus", "Unknown command"),
    ]
    for cmd, needle in checks:
        out = run_cmd(ws, cmd)
        ok = needle in out and out.endswith(PROMPT)
        print(f"[{'PASS' if ok else 'FAIL'}] {cmd}")
        if not ok:
            print(f"  got: {out!r}")
            failures += 1

    # one multi-command frame: the line assembler must split it
    out = run_cmd(ws, "version\nled", prompts=2)
    if out.count(PROMPT) >= 2 and "LED:" in out:
        print("[PASS] batched lines in one frame")
    else:
        print(f"[FAIL] batched lines: {out!r}")
        failures += 1

    ws.close()
    print("CLI WS " + ("PASS" if failures == 0 else f"FAIL ({failures})"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
