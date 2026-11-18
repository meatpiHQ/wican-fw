#!/usr/bin/env python3
"""What the data path benches of 2026-11-18 share.

The unit's web API through a tunnel, one console command over /ws/cli,
settings staged for a run and put back after it, and every loss counter of
the data path routes in one flat dict, so a bench can say "this counter
moved and no other did".

Used by can/mqtt_can_batch_bench.py, can/can_busoff_slots_bench.py,
system/log_tail_stall_bench.py and usb/usb_obd_latency_bench.py.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wsmin import WS, OP_TEXT  # noqa: E402

# a counter whose name holds one of these words counts something lost
LOSS_WORDS = ("drop", "lost", "refused", "missed", "overrun", "error", "cut",
              "oversize", "overflow", "malformed")
LOSS_ROUTES = ("/api/bridges", "/api/sockets", "/api/ws", "/api/can")


class Checks:
    """PASS / FAIL / SKIP lines and the verdict line at the end."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.passes, self.fails, self.skips = [], [], []
        self.t0 = time.time()

    def check(self, name, ok, detail=""):
        print(("PASS: " if ok else "FAIL: ") + name
              + ("  [" + str(detail) + "]" if detail != "" else ""), flush=True)
        (self.passes if ok else self.fails).append(name)
        return ok

    def skip(self, name, why):
        print("SKIP: " + name + "  [" + why + "]", flush=True)
        self.skips.append(name)

    def finish(self):
        print("%d checks, %d failed, %d skipped, %.0f s" % (
            len(self.passes) + len(self.fails), len(self.fails), len(self.skips),
            time.time() - self.t0), flush=True)
        if self.fails:
            print(self.verdict + " FAIL: " + ", ".join(self.fails))
            return 1
        print(self.verdict + " PASS" + (" (%d skipped)" % len(self.skips) if self.skips else ""))
        return 0


class Dut:
    """The unit's web API and console at host:port (a tunnel is fine)."""

    def __init__(self, base):
        self.host = base.split(":")[0]
        self.port = int(base.split(":")[1]) if ":" in base else 80
        self.base = "http://" + base

    def api(self, path, method="GET", body=None, timeout=10, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(errors="replace")
        if raw:
            return t
        return json.loads(t) if t.strip().startswith(("{", "[")) else t

    def get(self, path, timeout=10):
        try:
            return self.api(path, timeout=timeout)
        except Exception:
            return None

    def wait_up(self, secs=150):
        end = time.time() + secs
        while time.time() < end:
            try:
                self.api("/api/status", timeout=4)
                return True
            except Exception:
                time.sleep(2)
        return False

    def settled(self, uptime_s=30):
        """Wait until the unit has been up for @p uptime_s. In its first half
        minute it writes its flash on its own (the configuration copies are
        refreshed 20 s after a boot that follows a settings change), and a
        flash write holds the whole unit for tens of ms: a leg that counts
        every byte or every line starts after that (2026-11-18: two benches
        lost a few lines to it). Returns the uptime in seconds, -1 unknown."""
        end = time.time() + uptime_s + 20
        up = -1
        while time.time() < end:
            parts = str((self.get("/api/status") or {}).get("uptime", "")).split(":")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                up = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                if up >= uptime_s:
                    break
            time.sleep(1)
        return up

    def settings(self, comp):
        s = self.api("/api/settings/" + comp)
        for k in ("degraded", "pending_reboot"):
            s.pop(k, None)
        return s

    def ring_count(self, needle):
        try:
            return self.api("/api/logs/ring", raw=True).count(needle)
        except Exception:
            return -1

    def cli(self, line, marker=None, quiet_s=1.5, total_s=15):
        """One console command over a fresh /ws/cli; the text it printed.
        Returns at @p marker, or once nothing came for quiet_s."""
        ws = WS(self.host, self.port, "/ws/cli", timeout=8)
        out = ""
        try:
            ws.send((line + "\n").encode(), opcode=OP_TEXT)
            ws.settimeout(quiet_s)
            end = time.time() + total_s
            while time.time() < end:
                try:
                    _, payload = ws.recv_frame()
                except Exception:
                    break
                out += payload.decode("utf-8", "replace")
                if marker and marker in out:
                    break
        finally:
            ws.close()
        return out

    def health(self, wait_s=0):
        """@p wait_s: keep asking this long for an answer. A bench's first
        request: the caller's tunnel may not stand yet (the lookup of the
        Pi's name took 7 s on the PC of 2026-11-18) and a unit does not
        answer for some seconds after a restart."""
        end = time.time() + wait_s
        st = self.get("/api/status")
        while not st and time.time() < end:
            time.sleep(1)
            st = self.get("/api/status", timeout=4)
        st = st or {}
        return {"boot_count": st.get("boot_count"),
                "unexpected_resets": st.get("unexpected_resets"),
                "log_errors": (st.get("health") or {}).get("log_errors"),
                "version": st.get("version")}

    def losses(self):
        """Every loss counter of the data path routes: 'route:path' -> value."""
        out = {}
        for route in LOSS_ROUTES:
            _flatten(self.get(route), route, out)
        return out


def _flatten(node, path, out):
    if isinstance(node, dict):
        name = node.get("name")
        for k, v in node.items():
            _flatten(v, path + "." + (str(name) + "." if isinstance(name, str) and k == "stats" else "") + k, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            key = v.get("name") if isinstance(v, dict) and isinstance(v.get("name"), str) else str(i)
            _flatten(v, path + "[" + key + "]", out)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        leaf = path.rsplit(".", 1)[-1]
        if any(w in leaf for w in LOSS_WORDS):
            out[path] = node


def moved(before, after):
    """The loss counters that differ between two Dut.losses() dicts."""
    return {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}


class Staged:
    """The settings as found, what a run stages over them, and the way back."""

    def __init__(self, dut, comps, verdict):
        self.dut = dut
        self.verdict = verdict
        self.planned = 0
        self.found = {c: dut.settings(c) for c in comps}
        self.h0 = dut.health()
        # the unit counts its E lines per boot: what each boot of the run
        # added is summed up before the restart that ends it
        self._e_base = self.h0["log_errors"] or 0
        self.e_new = 0

    def copy(self, comp):
        return json.loads(json.dumps(self.found[comp]))

    def submit(self, changes, why):
        """PUT each component's whole object, then the one submit that applies."""
        for comp, obj in changes.items():
            self.dut.api("/api/settings/" + comp, "PUT", obj)
        self.e_new += (self.dut.health()["log_errors"] or 0) - self._e_base
        r = self.dut.api("/api/settings/submit", "POST")
        if isinstance(r, dict) and r.get("reboot"):
            self.restarted(why)
            self._e_base = 0
        else:
            self._e_base = self.dut.health()["log_errors"] or 0

    def restarted(self, why):
        self.planned += 1
        print("restart %d (%s)..." % (self.planned, why), flush=True)
        time.sleep(6)
        if not self.dut.wait_up():
            print(self.verdict + " FAIL: the unit did not come back after the restart (%s)" % why)
            sys.exit(1)
        time.sleep(3)

    def restore(self, ck):
        """Settings back as found; only planned restarts, no reset of its own,
        no E line in the boot that follows (the E count is per boot: a run
        that provokes a fault the firmware reports at E counts those lines
        itself, before this)."""
        self.submit(self.found, "the settings as found")
        back = {c: self.dut.settings(c) for c in self.found}
        ck.check("R1 settings back as found", back == self.found,
                 {c: "same" if back[c] == self.found[c] else "DIFFERS" for c in self.found})
        h = self.dut.health()
        ck.check("R2 only the planned restarts (%d), no reset of its own" % self.planned,
                 h["boot_count"] == self.h0["boot_count"] + self.planned
                 and h["unexpected_resets"] == self.h0["unexpected_resets"],
                 {"boot_count": (self.h0["boot_count"], h["boot_count"]),
                  "unexpected": (self.h0["unexpected_resets"], h["unexpected_resets"])})
        self.e_new += (h["log_errors"] or 0) - self._e_base
        ck.check("R3 no new E lines in any boot of the run", self.e_new == 0, self.e_new)
