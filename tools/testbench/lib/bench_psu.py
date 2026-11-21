"""The DUT's supply on the bench: ECU sim v2's programmable OBD pin 16.

Since 2026-11-09 the WiCAN under test is powered from the ECU simulator's
own OBD socket. ECU sim v2 sets the pin 16 voltage, switches the output and
reads the voltage and the current AT THE PIN, so the sleep and running
currents the benches judge are the DUT's alone (the first rig's bench
supply, an OWON P4305 over RS232, fed a harness shared with the first
simulator box; `owon_psu.py` is that driver, kept for a rig that still has
one). The REST surface is `GET/POST /api/power` over the simulator's USB
network link (`sim_net.py` finds it).

    from bench_psu import open_psu
    psu = open_psu(args.psu)          # "auto" = the simulator's supply, else
                                      # http://host = that simulator, COMx =
                                      # the legacy OWON on that port
    psu.set_voltage(12.5); psu.output(False); psu.meas_current()

The interface is OwonPsu's, so a bench runs on either supply:
set_voltage / set_current_limit / output / output_on / voltage_setpoint /
current_limit / meas_voltage / meas_current / sample_current / wait_current
/ idn / close, and a context manager.

SAFETY: every voltage path is clamped to HARD_MAX_V (15.0 V): the DUT's
automotive input is specced for 12 V systems. The simulator itself goes
higher, so the ceiling lives here; never raise it.

By hand (what `owon_psu.py status|cycle` did):

    python bench_psu.py                 setpoint, output, measured V and mA
    python bench_psu.py cycle           output off 4 s, on again, then the same
    python bench_psu.py set 13.5        a new setpoint (the ceiling applies)
    python bench_psu.py on|off          the output
    python bench_psu.py --psu COM50     the legacy OWON instead

A simulator reboot (a settings submit, `bench_bus.sim_set`) puts the supply
back to the simulator's boot defaults (13.8 V, on, on this bench): a bench
that moved the voltage and then reboots the simulator sets it again.

A POWER CUT OF A STATED LENGTH: `psu.hold_off(seconds)`. Why it needs a
method (2026-11-21): the DUT's own COM ports leave the PC with its power, and
a program on this PC then opens the simulator's debug port with DTR and RTS
asserted and closes it. That sequence resets the simulator's chip (and now
and then leaves it in its ROM download mode). Until the simulator firmware of
2026-11-21 the boot behind such a reset switched the output on again, so a
cut by `output(False)` lasted 4 to 12 s. That firmware keeps a port that was
switched off by command off (`held_off` on `/api/power`); this driver also
releases the debug port's lines when a supply is opened (a stray open then
resets nothing) and fetches a simulator that sits in its ROM back.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_net  # noqa: E402

#: absolute ceiling for anything this driver will command; the WiCAN bench
#: rule is "never above 15 V"
HARD_MAX_V = 15.0
MIN_V = 0.8                 # the simulator's own floor
AUTO = ("", "auto", None)


class SimPsu:
    """ECU sim v2's OBD supply through `/api/power`."""

    def __init__(self, base: str | None = None, timeout_s: float = 4.0):
        self.base = sim_net.sim_url(base)
        self.timeout_s = timeout_s
        self._set_mv = None             # the set point the bench last saw or asked for
        self.restarts = 0               # simulator restarts seen by _recover()
        sim_net.install(self.base)
        st = self._get()
        if not (st.get("tps_ok") and st.get("ina_ok")):
            raise OSError(f"the supply at {self.base} is not whole: "
                          f"tps_ok={st.get('tps_ok')} ina_ok={st.get('ina_ok')}")
        info = {}
        try:
            with urllib.request.urlopen(self.base + "/api/info", timeout=timeout_s) as r:
                info = json.loads(r.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        self.idn = "ECU sim v2 supply %s at %s" % (info.get("fw_version", "?"), self.base)
        #: the simulator keeps a port switched off by command off across a
        #: reset of itself (its firmware of 2026-11-21 or later)
        self.keeps_off = "held_off" in st
        self.rom_fetches = 0            # times the simulator was fetched out of its ROM
        self.release_debug_port()
        if st.get("set_mv", 0) > HARD_MAX_V * 1000 + 1:
            raise ValueError(f"the supply is set to {st['set_mv'] / 1000:.2f} V, over the "
                             f"bench ceiling of {HARD_MAX_V} V: lower it by hand first")

    # ---- transport --------------------------------------------------------

    def _http(self, method: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + "/api/power", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        last = None
        for _ in range(3):              # the link drops a request now and then
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    out = json.loads(r.read().decode("utf-8", errors="replace"))
                if not isinstance(out, dict):
                    raise ValueError(f"/api/power answered {out!r}")
                self._note(out)
                return out
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", errors="replace")[:200]
                raise ValueError(f"/api/power {method} {body}: {e.code} {txt}") from None
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = e
                time.sleep(0.3)
        # 2026-11-09: once, right after an `output off`, the simulator's REST
        # went away for 13 s and came back at its boot defaults (13.8 V, on),
        # a restart of the simulator (its console read `reset: USB`). A bench
        # in the middle of a power cycle must not die on that: wait for the
        # simulator, put the set point back, say so, and finish the request.
        if self._recover(f"{method} {body}"):
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                out = json.loads(r.read().decode("utf-8", errors="replace"))
            if isinstance(out, dict):
                self._note(out)
                return out
        raise OSError(f"the supply at {self.base} does not answer: {last}")

    def _note(self, st: dict) -> None:
        """Remember the set point the bench last saw or asked for."""
        if "set_mv" in st:
            self._set_mv = int(st["set_mv"])

    def _recover(self, during: str, limit_s: float = 45.0) -> bool:
        """The simulator restarted (or its link dropped): wait for its REST,
        re-apply the remembered set point, report. True when it is back."""
        t0 = time.time()
        fetched = False
        while time.time() - t0 < limit_s:
            try:
                with urllib.request.urlopen(self.base + "/api/power", timeout=2) as r:
                    st = json.loads(r.read().decode("utf-8", errors="replace"))
                break
            except (urllib.error.URLError, OSError, ValueError):
                # a boot takes about 7 s; a simulator still away then may sit
                # in its ROM download mode (the module's head note)
                if not fetched and time.time() - t0 > 9 and self._rom_port():
                    fetched = True
                    self._leave_rom()
                time.sleep(1)
        else:
            return False
        away = time.time() - t0
        want = getattr(self, "_set_mv", None)
        note = ""
        if want is not None and abs(int(st.get("set_mv", 0)) - want) > 10:
            body = json.dumps({"voltage_mv": want}).encode()
            req = urllib.request.Request(self.base + "/api/power", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    r.read()
                note = f"; set point {want / 1000:.2f} V re-applied (it read {st.get('set_mv', 0) / 1000:.2f} V)"
            except (urllib.error.URLError, OSError) as e:
                note = f"; re-applying the set point failed ({e})"
        print(f"bench_psu: WARNING the simulator's REST was away {away:.0f} s during "
              f"{during}: a restart of the simulator (output {'on' if st.get('output') else 'off'}"
              f"{note})", flush=True)
        self.restarts = getattr(self, "restarts", 0) + 1
        return True

    def _get(self) -> dict:
        return self._http("GET")

    def status(self) -> dict:
        """The simulator's reading: set_mv, limit_ma, vbus_mv, current_ma,
        power_mw, output, faults, sample_age_ms."""
        return self._get()

    # ---- control ----------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        if not 0.0 <= volts <= HARD_MAX_V:
            raise ValueError(f"refusing {volts} V (ceiling {HARD_MAX_V} V)")
        mv = int(round(max(volts, MIN_V) * 1000))
        self._set_mv = mv
        st = self._http("POST", {"voltage_mv": mv})
        if abs(int(st.get("set_mv", -1)) - mv) > 10:
            raise ValueError(f"set {mv} mV, the supply reports {st.get('set_mv')} mV")
        time.sleep(0.15)

    def set_current_limit(self, amps: float) -> None:
        ma = int(round(amps * 1000 / 50.0)) * 50       # the supply's 50 mA steps
        self._http("POST", {"limit_ma": max(50, min(6000, ma))})

    def output(self, on: bool) -> None:
        before = self.restarts
        st = self._http("POST", {"output": bool(on)})
        if bool(st.get("output")) != bool(on) and self.restarts == before:
            raise ValueError(f"output {'on' if on else 'off'} requested, the supply "
                             f"reports {st.get('output')}")
        if bool(st.get("output")) != bool(on):
            # the simulator restarted under the request and, on its firmware
            # from before 2026-11-21, booted with its own default (on): the
            # DUT lost power either way; a bench that wanted the output off
            # gets it off again here
            self._http("POST", {"output": bool(on)})
        time.sleep(0.4)

    def clear_faults(self) -> None:
        self._http("POST", {"clear_faults": True})

    # ---- a power cut of a stated length ------------------------------------

    def _setting(self, method: str = "GET", body: dict | None = None, path: str = "/api/settings/obd_power",
                 tries: int = 40):
        """The simulator's own settings route; it restarts on a submit, so a
        request is tried until it answers. None when it never did."""
        data = None if body is None else json.dumps(body).encode()
        for _ in range(tries):
            try:
                req = urllib.request.Request(self.base + path, data=data, method=method,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.loads(r.read().decode("utf-8", errors="replace"))
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(1.5)
        return None

    def _boot_output(self, on: bool) -> None:
        cur = self._setting()
        if cur is None:
            raise OSError(f"the simulator at {self.base} does not answer its settings route")
        new = {k: v for k, v in cur.items() if k not in ("degraded", "pending_reboot")}
        new["output_on_boot"] = bool(on)
        self._setting("PUT", new, tries=6)
        self._setting("POST", {}, path="/api/settings/submit", tries=2)

    # ---- the simulator's debug port (the module's head note) --------------

    @staticmethod
    def _usb_port(vid: int, pid: int):
        try:
            import serial.tools.list_ports as list_ports
        except ImportError:
            return None
        for port in list_ports.comports():
            if (port.vid, port.pid) == (vid, pid):
                return port.device
        return None

    def _debug_port(self):
        """The simulator's USB Serial/JTAG console port, or None."""
        return self._usb_port(0x303A, 0x1001)

    def _rom_port(self):
        """The chip's ROM download mode shows as another USB device."""
        return self._usb_port(0x303A, 0x0020)

    def release_debug_port(self) -> bool:
        """Open the simulator's debug port once with DTR and RTS released and
        close it. Windows keeps those line settings for the port, so a program
        that opens it afterwards without a thought for its lines no longer
        sends the sequence that resets the chip (three power cuts in a row
        without a reset on 2026-11-21, against a reset at every cut before).
        Best effort: no pyserial, no such port, or a port in use = False."""
        dev = self._debug_port()
        if dev is None:
            return False
        try:
            import serial
            con = serial.Serial()
            con.port = dev
            con.baudrate = 115200
            con.dtr = False
            con.rts = False
            con.open()
            con.close()
            return True
        except Exception:               # noqa: BLE001  (in use by a console capture: fine)
            return False

    def _leave_rom(self) -> bool:
        """The simulator sits in its ROM download mode: esptool's hard reset
        through the debug port starts the firmware again."""
        dev = self._debug_port()
        if dev is None:
            print("bench_psu: WARNING the simulator sits in its ROM download mode and its debug port was "
                  "not found: reset it by hand (the EN button)", flush=True)
            return False
        import subprocess
        r = subprocess.run([sys.executable, "-m", "esptool", "--chip", "esp32s31", "-p", dev, "-b", "115200",
                            "--no-stub", "--before", "no-reset", "--after", "hard-reset", "read-mac"],
                           capture_output=True, text=True, timeout=90)
        self.rom_fetches += 1
        ok = r.returncode == 0
        print("bench_psu: WARNING the simulator was in its ROM download mode (a program on this PC opened its "
              "debug port %s with DTR and RTS asserted): %s" % (dev, "fetched back with esptool" if ok else
              "esptool could not fetch it back (%s): reset it by hand" % (r.stdout or r.stderr or "").strip()[-120:]),
              flush=True)
        return ok

    def _peek(self):
        """One reading, or None while the simulator does not answer."""
        try:
            with urllib.request.urlopen(self.base + "/api/power", timeout=2) as r:
                st = json.loads(r.read().decode("utf-8", errors="replace"))
            return st if isinstance(st, dict) else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def hold_off(self, seconds: float) -> float:
        """The DUT without power for `seconds`, for real; returns the time the
        pin measured under 2 V. With the simulator firmware of 2026-11-21 it is
        output off, wait, output on: the port stays off across a reset of the
        simulator, and a simulator found in its ROM is fetched back. Raises
        when the supply comes back by itself. An older firmware goes through
        its setting (`_hold_off_by_setting`)."""
        if not self.keeps_off:
            return self._hold_off_by_setting(seconds)
        self.release_debug_port()
        self._http("POST", {"output": False})
        t_off = None
        t0 = time.time()
        while time.time() - t0 < 30 and t_off is None:
            st = self._peek()
            if st and not st.get("output") and st.get("vbus_mv", 99999) < 2000:
                t_off = time.time()
            else:
                time.sleep(0.3)
        if t_off is None:
            raise OSError("the supply did not go off")
        away_since = None
        while time.time() - t_off < seconds:
            st = self._peek()
            if st is None:
                away_since = away_since or time.time()
                if time.time() - away_since > 9 and self._rom_port():
                    self._leave_rom()
                    away_since = None
            else:
                away_since = None
                if st.get("output") or st.get("vbus_mv", 0) > 2000:
                    self._http("POST", {"output": True})
                    raise OSError("the supply came back by itself %.0f s into a cut of %.0f s (output %s, %s mV)"
                                  % (time.time() - t_off, seconds, st.get("output"), st.get("vbus_mv")))
            time.sleep(1)
        self.output(True)
        t0 = time.time()
        while time.time() - t0 < 30:
            st = self._peek()
            if st and st.get("output") and st.get("vbus_mv", 0) > 9000:
                break
            time.sleep(0.5)
        else:
            raise OSError(f"THE SUPPLY DID NOT COME BACK: switch the output on at {self.base} by hand")
        return time.time() - t_off

    def _hold_off_by_setting(self, seconds: float) -> float:
        """The way through the simulator's setting, for its firmware from
        before 2026-11-21, which switched the output on again at every reset
        of itself (its setting `obd_power.output_on_boot`). The setting is
        the switch: false + submit = the simulator restarts with the output
        off and stays so, true + submit = it restarts with the output on.
        The setting is left true, as the bench has it; a failure to switch
        the supply back on raises with the hand step named."""
        want_mv = self._set_mv
        self._boot_output(False)
        t_off = None
        t0 = time.time()
        while time.time() - t0 < 40 and t_off is None:
            st = self._setting(path="/api/power", tries=1)
            if st and st.get("vbus_mv", 99999) < 2000:
                t_off = time.time()
            time.sleep(0.5)
        try:
            if t_off is None:
                raise OSError("the supply did not go off")
            time.sleep(seconds)
        finally:
            self._boot_output(True)
            t0 = time.time()
            on = False
            while time.time() - t0 < 60 and not on:
                st = self._setting(path="/api/power", tries=1)
                on = bool(st and st.get("output") and st.get("vbus_mv", 0) > 9000)
                time.sleep(0.5)
            if not on:
                raise OSError("THE SUPPLY DID NOT COME BACK: set obd_power.output_on_boot true on the "
                              f"simulator at {self.base} by hand and submit")
        off_s = time.time() - t_off
        if want_mv is not None:
            self.set_voltage(want_mv / 1000.0)     # the simulator's boot default otherwise
        return off_s

    def close(self) -> None:
        pass

    def __enter__(self) -> "SimPsu":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- readback ---------------------------------------------------------

    def output_on(self) -> bool:
        return bool(self._get().get("output"))

    def voltage_setpoint(self) -> float:
        return float(self._get().get("set_mv", 0)) / 1000.0

    def current_limit(self) -> float:
        return float(self._get().get("limit_ma", 0)) / 1000.0

    def meas_voltage(self) -> float:
        return float(self._get().get("vbus_mv", 0)) / 1000.0

    def meas_current(self) -> float:
        """Amps at pin 16 (the simulator samples every 250 ms)."""
        return float(self._get().get("current_ma", 0)) / 1000.0

    def faults(self) -> dict:
        return dict(self._get().get("faults") or {})

    def sample_current(self, secs: float, interval_s: float = 0.5) -> list[float]:
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

    # the SCPI pair of the OWON driver: a bench still using them is told so
    def send(self, cmd: str, settle_s: float = 0.15) -> None:
        raise NotImplementedError(f"no SCPI on the simulator's supply ({cmd!r}): "
                                  "use the driver's methods")

    def query(self, cmd: str, wait_s: float = 0.25) -> str:
        raise NotImplementedError(f"no SCPI on the simulator's supply ({cmd!r}): "
                                  "use the driver's methods")


def open_psu(spec=None, verbose: bool = True):
    """The bench supply for `spec`: "auto" (default) = ECU sim v2's supply,
    else the legacy OWON on the FTDI port the detector finds; "http://..." =
    that simulator's supply; "COMx" = the OWON on that port. SystemExit with
    the reason when nothing answers."""
    spec = None if spec in AUTO else str(spec)
    if spec is None or spec.lower() == "sim" or spec.startswith("http"):
        base = spec if spec and spec.startswith("http") else None
        try:
            psu = SimPsu(base)
            if verbose:
                print(f"supply: {psu.idn}", flush=True)
            return psu
        except (OSError, ValueError) as e:
            if spec is not None:
                raise SystemExit(f"bench_psu: {e}")
            why_sim = str(e)
        # the legacy rig: an OWON on an FTDI port
        try:
            import bench_ports  # noqa: E402
            port = bench_ports.port("psu")
        except Exception:  # noqa: BLE001
            port = None
        if port:
            from owon_psu import OwonPsu  # noqa: E402
            try:
                psu = OwonPsu(port)
                if verbose:
                    print(f"supply: {psu.idn} on {port} (the legacy OWON)", flush=True)
                return psu
            except (OSError, AssertionError, ValueError) as e:
                raise SystemExit(f"bench_psu: no supply: the simulator's did not answer "
                                 f"({why_sim}) and the OWON on {port} failed ({e})")
        raise SystemExit("bench_psu: no supply: ECU sim v2's /api/power did not answer "
                         f"({why_sim}) and no FTDI port for an OWON is present; is the "
                         "simulator's USB link up? (python tools/testbench/lib/sim_net.py)")
    from owon_psu import OwonPsu  # noqa: E402
    try:
        psu = OwonPsu(spec)
    except (OSError, AssertionError, ValueError) as e:
        raise SystemExit(f"bench_psu: the OWON on {spec}: {e}")
    if verbose:
        print(f"supply: {psu.idn} on {spec} (the legacy OWON)", flush=True)
    return psu


def main() -> int:
    """The supply by hand; the `status` line keeps the OWON helper's shape
    (`... measured 13.87 V, 49 mA (median of 8; 48 to 52)`) so the release
    test's reading of it is unchanged."""
    import argparse

    ap = argparse.ArgumentParser(description="The bench supply by hand")
    ap.add_argument("what", nargs="?", default="status",
                    choices=("status", "cycle", "set", "on", "off"))
    ap.add_argument("value", nargs="?", help="volts for `set`")
    ap.add_argument("--psu", default="auto",
                    help="auto (ECU sim v2's supply), http://host, or the OWON's COM port")
    ap.add_argument("--off-s", type=float, default=4.0, help="the cycle's off time")
    a = ap.parse_args()
    psu = open_psu(a.psu)
    try:
        if a.what == "cycle":
            psu.output(False)
            print("output off", flush=True)
            time.sleep(a.off_s)
            psu.output(True)
            print("output on", flush=True)
            time.sleep(2)
        elif a.what == "set":
            if a.value is None:
                raise SystemExit("set needs a voltage")
            psu.set_voltage(float(a.value))
            print(f"setpoint {float(a.value):.2f} V", flush=True)
            time.sleep(0.5)
        elif a.what in ("on", "off"):
            psu.output(a.what == "on")
            print(f"output {a.what}", flush=True)
            time.sleep(1)
        amps = sorted(psu.sample_current(2, 0.25))
        print(f"setpoint {psu.voltage_setpoint():.2f} V, output "
              f"{'on' if psu.output_on() else 'OFF'}, measured "
              f"{psu.meas_voltage():.2f} V, {amps[len(amps) // 2] * 1000:.0f} mA "
              f"(median of {len(amps)}; {amps[0] * 1000:.0f} to "
              f"{amps[-1] * 1000:.0f})")
    finally:
        psu.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
