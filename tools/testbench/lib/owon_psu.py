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


def main() -> int:
    """By hand: what the supply reads, and a power cycle of the DUT.

        python owon_psu.py            setpoint, output, measured V and mA
        python owon_psu.py cycle      output off 4 s, on again, then the same

    The current tells a DUT that went quiet apart without touching it
    (TESTING.md, "When the DUT disappears in the middle of a test"): about
    47 mA at 13.5 V = parked or asleep, 73 mA = the ROM's download mode,
    160 mA = awake. Opens the PSU's port only.
    """
    import argparse

    import bench_ports

    ap = argparse.ArgumentParser(description="The bench PSU by hand")
    ap.add_argument("what", nargs="?", default="status",
                    choices=("status", "cycle"))
    ap.add_argument("--port", default="auto")
    a = ap.parse_args()
    with OwonPsu(bench_ports.resolve(a.port, "psu", "COM2016")) as psu:
        try:
            psu.meas_current()  # a cold OWON's first answer is not one
        except (AssertionError, ValueError):
            pass
        if a.what == "cycle":
            psu.output(False)
            print("output off", flush=True)
            time.sleep(4)
            psu.output(True)
            print("output on", flush=True)
            time.sleep(2)
        amps = sorted(psu.sample_current(2, 0.25))
        print(f"setpoint {psu.voltage_setpoint():.2f} V, output "
              f"{'on' if psu.output_on() else 'OFF'}, measured "
              f"{psu.meas_voltage():.2f} V, {amps[len(amps) // 2] * 1000:.0f} mA "
              f"(median of {len(amps)}; {amps[0] * 1000:.0f} to "
              f"{amps[-1] * 1000:.0f})")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
