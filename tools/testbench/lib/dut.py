"""Serial driver for the WiCAN DUT running a HIL test app.

Thin pyserial wrapper: hard-reset via RTS (same auto-reset circuit esptool
uses), expect() over an accumulating buffer, and line commands for the HIL
app's protocol (SET / RESTART / STATUS / SCAN — see
components/wifi_manager/test_apps_hil/main/hil_main.c).
"""
from __future__ import annotations

import json
import re
import time

import serial


class Dut:
    def __init__(self, port: str, baud: int = 115200):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.buf = bytearray()
        self.scan = bytearray()  # since reset_health(): E/W-line tally

    def close(self) -> None:
        self.ser.close()

    # ---- health tally (2026-07-19: per-test error budgets) ----------------

    def reset_health(self) -> None:
        """Start a fresh E/W tally (call at test start)."""
        self.scan.clear()

    def health(self) -> tuple[int, int]:
        """(error_lines, warning_lines) seen since reset_health().
        Only counts output the test actually pumped through expect()."""
        return len(self.health_lines("E")), len(self.health_lines("W"))

    def health_lines(self, level: str = "E") -> list[str]:
        """The actual E/W lines since reset_health() (for reports)."""
        plain = re.sub(rb"\x1b\[[0-9;]*m", b"", bytes(self.scan))
        pat = rb"^" + level.encode() + rb" \(\d+\).*$"
        return [m.decode(errors="replace")
                for m in re.findall(pat, plain, re.M)]

    # ---- io ---------------------------------------------------------------

    def hard_reset(self) -> None:
        self.buf.clear()
        self.ser.reset_input_buffer()
        self.ser.dtr = False
        self.ser.rts = True
        time.sleep(0.1)
        self.ser.rts = False

    def sendline(self, line: str) -> None:
        self.ser.write((line + "\n").encode())
        self.ser.flush()

    def expect(self, pattern: str, timeout_s: float = 30) -> re.Match:
        """Wait until `pattern` (regex) appears in the serial stream.

        Consumes the buffer through the end of the match (real expect
        semantics) so a later expect can never re-match stale output.
        """
        deadline = time.time() + timeout_s
        rx = re.compile(pattern.encode())
        while True:
            # match on an immutable snapshot: a re.Match over the live
            # bytearray would read garbage once we consume from it below
            m = rx.search(bytes(self.buf))
            if m:
                del self.buf[:m.end()]
                return m
            if time.time() >= deadline:
                break
            chunk = self.ser.read(4096)
            if chunk:
                self.buf.extend(chunk)
                self.scan.extend(chunk)
        tail = self.buf[-2000:].decode(errors="replace")
        raise AssertionError(
            f"timeout waiting for /{pattern}/; last output:\n{tail}")

    # ---- HIL app protocol ---------------------------------------------------

    def wait_ready(self, timeout_s: float = 30) -> None:
        self.expect(r"HIL READY", timeout_s)

    def set_wifi(self, obj: dict, timeout_s: float = 10) -> None:
        self.sendline("SET " + json.dumps(obj))
        self.expect(r"SET ok=1", timeout_s)

    def restart(self) -> None:
        self.sendline("RESTART")
        self.expect(r"RESTARTING", 5)

    def status(self, timeout_s: float = 10) -> str:
        self.sendline("STATUS")
        m = self.expect(r"STATUS enabled=\d sta=\d ip=\S* ap=\d clients=\d+"
                        r" ap_ch=\d+ radio_ch=\d+",
                        timeout_s)
        return m.group(0).decode()
