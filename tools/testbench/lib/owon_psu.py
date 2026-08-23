"""OWON P4305 bench power supply driver (RS232, SCPI).

The PSU plays the car battery for sleep-mode tests: voltage steps across
the sleep/wake thresholds and current readback to prove the DUT actually
slept. Protocol verified 2026-07-20 against P4305 FW V1.8.0 on COM2016:
115200 baud, LF-terminated SCPI, queries answer, set commands are silent.

SAFETY: every voltage path is clamped to HARD_MAX_V (15.0 V) and open()
programs the same ceiling into the instrument (VOLT:LIM) so not even a
front-panel fat-finger can exceed it. Never raise HARD_MAX_V — the DUT's
automotive input is specced for 12 V systems.
"""
from __future__ import annotations

import time

import serial

#: absolute ceiling for anything this driver will command OR allow the
#: instrument to produce; the WiCAN bench rule is "never above 15 V"
HARD_MAX_V = 15.0


class OwonPsu:
    def __init__(self, port: str, baud: int = 115200):
        # \\.\ prefix: Windows needs it for COM ports numbered > 9
        dev = f"\\\\.\\{port}" if port.upper().startswith("COM") else port
        self.ser = serial.Serial(dev, baud, timeout=1)
        time.sleep(0.2)
        idn = self.query("*IDN?")
        assert "P4305" in idn, f"unexpected instrument on {port}: {idn!r}"
        self.idn = idn
        # program the bench ceiling into the instrument itself
        self.send(f"VOLT:LIM {HARD_MAX_V:.3f}")
        lim = float(self.query("VOLT:LIM?"))
        assert lim <= HARD_MAX_V + 0.001, f"VOLT:LIM readback {lim}"

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "OwonPsu":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- protocol ---------------------------------------------------------

    def send(self, cmd: str, settle_s: float = 0.15) -> None:
        """Set command — the P4305 sends no reply."""
        self.ser.write((cmd + "\n").encode())
        time.sleep(settle_s)

    def query(self, cmd: str, wait_s: float = 0.25) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        time.sleep(wait_s)
        resp = self.ser.read(self.ser.in_waiting or 1)
        out = resp.decode(errors="replace").strip()
        assert out, f"no reply to {cmd!r}"
        return out

    # ---- control ----------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        if not 0.0 <= volts <= HARD_MAX_V:
            raise ValueError(f"refusing {volts} V (ceiling {HARD_MAX_V} V)")
        self.send(f"VOLT {volts:.3f}")

    def set_current_limit(self, amps: float) -> None:
        self.send(f"CURR {amps:.3f}")

    def output(self, on: bool) -> None:
        self.send(f"OUTP {1 if on else 0}", settle_s=0.4)

    # ---- readback ---------------------------------------------------------

    def output_on(self) -> bool:
        return self.query("OUTP?") == "1"

    def voltage_setpoint(self) -> float:
        return float(self.query("VOLT?"))

    def current_limit(self) -> float:
        return float(self.query("CURR?"))

    def meas_voltage(self) -> float:
        return float(self.query("MEAS:VOLT?"))

    def meas_current(self) -> float:
        return float(self.query("MEAS:CURR?"))

    def sample_current(self, secs: float,
                       interval_s: float = 0.5) -> list[float]:
        """Measured output current sampled over a window (amps)."""
        out = []
        deadline = time.time() + secs
        while time.time() < deadline:
            out.append(self.meas_current())
            time.sleep(interval_s)
        return out

    def wait_current(self, predicate, timeout_s: float,
                     interval_s: float = 0.5) -> float | None:
        """Poll measured current until predicate(amps) is true.
        Returns the matching reading, or None on timeout."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            amps = self.meas_current()
            if predicate(amps):
                return amps
            time.sleep(interval_s)
        return None
