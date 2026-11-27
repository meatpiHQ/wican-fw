#!/usr/bin/env python3
"""A body CAN bus on the OBD connector's CAN pins: the second node that acknowledges.

Some vehicles carry a bus on pins 6 and 14 that OBD is never asked on (a 2008
Fiat Ducato: a body bus at 50 kbit/s there, the engine ECU on the K-line;
GitHub issue #1022). ECU sim v2 plays it (bench_bus.sim_body_bus), and a bus
has more than one node: without an acknowledge the simulator repeats one frame
for ever, which is traffic too but not what a car puts on the wire. The PCAN
adapter is that second node: in normal mode at the bus's bit rate it
acknowledges every frame and sends nothing of its own.

Which of the adapter's two channels sits on the unit's bus changes with the
bench's wiring (TESTING.md, Troubleshooting), so nobody names it: both
channels listen first (listen-only sends nothing, not an acknowledge and not
an error frame, so the look is safe on the channel that sits on another bus)
and the one that reads the simulator's frames is taken.

  python body_bus.py [--kbps 50] [--seconds 10]     look, acknowledge, count

As a module: Acker(kbps).start() -> the channel or None (no adapter, no
python-can, or neither channel reads the bus: the caller goes on without an
acknowledge and says so); .stop() -> (frames, error frames).
"""
import sys
import threading
import time

CHANNELS = ("PCAN_USBBUS1", "PCAN_USBBUS2")


def _open(channel, kbps, listen_only):
    import can
    last = None
    for _ in range(2):                  # the first open at a new bit rate can fail once ("irregularities were registered")
        try:
            if listen_only:
                return can.Bus(interface="pcan", channel=channel, bitrate=kbps * 1000, state=can.BusState.PASSIVE)
            return can.Bus(interface="pcan", channel=channel, bitrate=kbps * 1000)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5)
    raise last


def look(kbps, seconds=2.0):
    """{channel: (frames, error frames)} of a listen-only look at both channels; a channel that does not open is left out."""
    seen = {}
    for ch in CHANNELS:
        try:
            bus = _open(ch, kbps, True)
        except Exception:  # noqa: BLE001
            continue
        n = err = 0
        end = time.time() + seconds
        try:
            while time.time() < end:
                m = bus.recv(0.2)
                if m is None:
                    continue
                if m.is_error_frame:
                    err += 1
                else:
                    n += 1
        finally:
            bus.shutdown()
        seen[ch] = (n, err)
    return seen


class Acker:
    """The acknowledging node of a bus at `kbps`, on whichever channel reads it."""

    def __init__(self, kbps):
        self.kbps = kbps
        self.channel = None
        self.frames = 0
        self.errors = 0
        self._bus = None
        self._run = False
        self._thread = None

    def start(self):
        try:
            seen = look(self.kbps)
        except ImportError:
            return None
        best = max(seen, key=lambda ch: seen[ch][0], default=None)
        if best is None or seen[best][0] == 0:
            return None
        try:
            self._bus = _open(best, self.kbps, False)
        except Exception:  # noqa: BLE001
            return None
        self.channel = best
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return best

    def _loop(self):
        while self._run:
            try:
                m = self._bus.recv(0.1)
            except Exception:  # noqa: BLE001
                break
            if m is None:
                continue
            if m.is_error_frame:
                self.errors += 1
            else:
                self.frames += 1

    def stop(self):
        self._run = False
        if self._thread is not None:
            self._thread.join(2)
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
        return self.frames, self.errors


def main():
    args = sys.argv[1:]

    def opt(name, default):
        return args[args.index(name) + 1] if name in args else default

    kbps, seconds = int(opt("--kbps", "50")), int(opt("--seconds", "10"))
    a = Acker(kbps)
    ch = a.start()
    if ch is None:
        print("BODY BUS: no channel of the adapter reads a bus at %d kbit/s" % kbps)
        return 1
    print("BODY BUS: %s acknowledges at %d kbit/s for %d s" % (ch, kbps, seconds), flush=True)
    time.sleep(seconds)
    frames, errors = a.stop()
    print("BODY BUS: %d frames, %d error frames" % (frames, errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
