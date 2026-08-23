#!/usr/bin/env python3
"""DWC2 kill-vs-ISR race hammer (run from the PC, drives via rpi001).

Targets the 2026-07-17 crash class: `usbh_kill_urb` from task context
racing a DWC2 channel-completion IRQ (LoadProhibited in
`dwc2_chan_free(NULL)` reading chan->urb; hit twice under WG/DERP
traffic + once in the TS teardown window). The 3-layer spinlock fix
lives in cherryusb's `usb_hc_dwc2.c`; the WG/TS 10+ min soaks passed,
this bench is the TARGETED stress.

Race levers (all remote, no unplugging):
- The usb_acm_cli RX task polls bulk-IN with a 200 ms timeout — every
  quiet expiry is a kill_urb. Flooding ACM commands with tiny
  `timeout_ms` makes response bytes land asynchronously around those
  kill instants (IN-channel kill-vs-completion).
- fw iperf UDP client blasting out the NCM interface saturates
  OUT-channel completions (the original crash was an OUT-channel IRQ).
- `iperf -a` mid-blast + short-t sessions churn socket teardown while
  OUT URBs are in flight (the TS-teardown-window geometry).

Physical setup: espnetlink dongle on the WiCAN USB connector (host
role), `usb_host_manager` + `usb_acm_cli` enabled. The dongle's NCM
gateway answers on the point-to-point subnet (DUT side e.g.
192.168.7.2 -> blast target .1).

Stages per round (default 2 rounds — the repeat-it rule):
  A acm_kill_flood   60 s mixed-timeout ACM command flood, idle bus
  B blast_plus_acm   45 s NCM UDP blast + the same ACM flood on top
  C teardown_churn   12x { 2 s blast -> `iperf -a` mid-flight -> ACM
                     round -> 1 s idle }
Invariants after every stage: usb attaches count UNCHANGED (no silent
re-enumeration), eth_connected + driver intact, ACM `ver` answers,
restart seq unchanged (no panic/reboot), /api/faults empty.

Expected final line: DWC2 HAMMER PASS
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sleep"))
from sleep_bench_test import PiHttp  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi_sh(host, cmd, stdin=None, timeout=180):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd],
                       input=stdin, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---- on-Pi worker scripts (ASCII only: they travel over ssh stdin) -------

ACM_FLOOD = r"""
import json, time, urllib.request, sys
base = sys.argv[1]; dur = float(sys.argv[2])
CMDS = [("ver", 1200), ("lte -s", 60), ("help", 60), ("ver", 60),
        ("lte -s", 1200)]
n = ok = err = 0
t0 = time.time()
while time.time() - t0 < dur:
    cmd, tmo = CMDS[n % len(CMDS)]
    n += 1
    body = json.dumps({"cmd": cmd, "timeout_ms": tmo}).encode()
    req = urllib.request.Request(base + "/api/usb/acm/cmd", data=body,
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode() or "{}")
        ok += 1 if d.get("ok") else 0
    except Exception:
        err += 1
print("FLOOD n=%d ok=%d err=%d" % (n, ok, err))
"""

WS_CLI = r"""
import sys, time, websocket
ip = sys.argv[1]; cmd = sys.argv[2]; wait = float(sys.argv[3])
ws = websocket.create_connection("ws://%s/ws/cli" % ip, timeout=5)
ws.settimeout(2)
ws.send(cmd + "\n")
out = ""
t0 = time.time()
while time.time() - t0 < wait:
    try:
        out += ws.recv()
    except Exception:
        break
ws.close()
print(out[:400])
"""


class Rig:
    def __init__(self, host):
        self.host = host
        self.dut = PiHttp(host, "auto")

    def ip(self):
        return self.dut.base.split("//")[1]

    def post_json(self, path, obj, timeout_s=15):
        rc, out = pi_sh(self.host,
                        f"curl -s -m {timeout_s} -X POST "
                        f"-H 'Content-Type: application/json' -d @- "
                        f"{self.dut.base}{path}", stdin=json.dumps(obj))
        try:
            return json.loads(out) if rc == 0 and out else None
        except ValueError:
            return None

    def acm_flood(self, dur_s):
        rc, out = pi_sh(self.host,
                        f"python3 - {self.dut.base} {dur_s}",
                        stdin=ACM_FLOOD, timeout=dur_s + 60)
        return out.strip().splitlines()[-1] if out.strip() else "no output"

    def ws_cli(self, cmd, wait=2.0):
        rc, out = pi_sh(self.host,
                        f"python3 - {self.ip()} \"{cmd}\" {wait}",
                        stdin=WS_CLI, timeout=wait + 30)
        return out

    def snap(self):
        usb = self.dut.get("/api/usb") or {}
        hist = self.dut.get("/api/restart/history") or {}
        seq = ((hist.get("records") or [{}])[0]).get("seq", -1)
        return usb, seq

    def verify(self, tag, usb0, seq0):
        """The post-stage invariants — any drift = the hammer drew blood."""
        # twin-roam tolerant: re-discover before judging
        if self.dut.get("/api/usb", timeout_s=4) is None:
            self.dut.discover()
        usb, seq = self.snap()
        check(f"{tag}: no re-enumeration",
              usb.get("attaches") == usb0.get("attaches"),
              f"attaches {usb0.get('attaches')} -> {usb.get('attaches')}")
        check(f"{tag}: link intact",
              usb.get("eth_connected") and usb.get("device_present") and
              usb.get("driver") == usb0.get("driver"),
              json.dumps({k: usb.get(k) for k in
                          ("eth_connected", "driver", "ip")}))
        check(f"{tag}: no reboot", seq == seq0, f"seq {seq0} -> {seq}")
        r = self.post_json("/api/usb/acm/cmd",
                           {"cmd": "ver", "timeout_ms": 3000})
        check(f"{tag}: ACM alive",
              bool(r and r.get("ok") and "ESPNetLink" in
                   (r.get("response") or "")),
              (r or {}).get("response", "")[:40].replace("\r", " "))
        faults = (self.dut.get("/api/faults") or {}).get("faults")
        check(f"{tag}: faults empty", faults == [], json.dumps(faults))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-host", default="rpi001")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--churn", type=int, default=12)
    args = ap.parse_args()

    rig = Rig(args.bench_host)
    usb0, seq0 = rig.snap()
    print("usb:", json.dumps(usb0), "seq:", seq0)
    if not (usb0.get("host_active") and usb0.get("eth_connected")):
        print("DWC2 HAMMER FAIL: dongle not up (enable usb_host_manager "
              "+ usb_acm_cli, plug the espnetlink dongle)")
        return 1
    # blast target = the dongle's side of the point-to-point NCM subnet
    gw = ".".join(usb0.get("ip", "").split(".")[:3]) + ".1"
    print(f"NCM blast target: {gw}")

    for rnd in range(1, args.rounds + 1):
        print(f"=== ROUND {rnd}/{args.rounds}:")

        print("--- A: acm_kill_flood (60 s)")
        print(" ", rig.acm_flood(60))
        rig.verify(f"r{rnd}A", usb0, seq0)

        print("--- B: NCM UDP blast + acm flood (45 s)")
        out = rig.ws_cli(f"iperf -c {gw} -u -t 45 -b 20", wait=2)
        assert "usage" not in out and "bad argument" not in out, \
            f"iperf refused: {out[:150]}"
        print(" ", rig.acm_flood(40))
        time.sleep(8)  # let the session finish
        print(" ", rig.ws_cli("iperf -r", wait=2).strip()[:150])
        rig.verify(f"r{rnd}B", usb0, seq0)

        print(f"--- C: teardown_churn ({args.churn} cycles)")
        for _ in range(args.churn):
            rig.ws_cli(f"iperf -c {gw} -u -t 5 -b 20", wait=0.5)
            time.sleep(1.2)               # mid-flight...
            rig.ws_cli("iperf -a", wait=0.5)   # ...abort = teardown kill
            rig.post_json("/api/usb/acm/cmd",
                          {"cmd": "lte -s", "timeout_ms": 60})
            time.sleep(1)
        rig.verify(f"r{rnd}C", usb0, seq0)

    if fails:
        print("DWC2 HAMMER FAIL:", ", ".join(fails))
        return 1
    print("DWC2 HAMMER PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
