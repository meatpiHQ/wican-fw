#!/usr/bin/env python3
"""The OBD chip's bring-up: the warm start and the PP 0F guard  ->  OBD BRINGUP PASS / FAIL

Two bring-up changes of 2026-11-02 (Ali), both after that night's benches
(test-reports/logs/atz_soft_reset_2026-11-02, pp0f_rewrite_2026-11-02):

  warm start    chip_soft_reset sends ATWS instead of ATZ. The MIC3624's ATZ
                answer began with junk bytes (00, 00 F0 FD 00 00, BF 80 04)
                in 5 of 16 probes and a NUL among them ended the C string
                before "ELM": about every third software restart since
                2026-10-19 logged "soft reset not confirmed" and paid a reset
                pulse plus 1.5 s. ATWS answered clean 12 of 12 times in 40 ms.
  PP 0F guard   the bring-up reads ATPPS and writes PP 0F back (SV 95 + ON)
                only when it is not 95 or off: STSLCS does not report it, so
                a chip image or a tool that reset it would have stayed that
                way. Counted in eeprom_guard.pp_restores.

  as found      the chip ready and unclaimed; the report's ATPPS reads
                0F:95 N; AutoPID's state noted (never changed)
  warm start    N planned restarts (default 3). Before each: ATTP7 over
                /ws/obd (RAM only, no request, so no store write) and ATDPN
                reads 7. After the boot the log ring must carry "chip awake;
                warm start (ATWS)" and "started: chip ... memory off", not
                "soft reset not confirmed" nor "chip not ready; hardware
                reset"; "bring-up done in N ms" under 1500; over /ws/obd
                ATDPN reads the stored protocol again (the warm start dropped
                the session; with AutoPID on that check is skipped);
                memory_off 1 and pp_restores 0
  PP 0F fault   the wire probe sends ATPP0FOFF (one EEPROM write: the enable
                flag; the chip applies the vendor's D5 at its next reset);
                the report reads 0F:95 F; a planned restart; the ring carries
                "PP 0F reads 95 off (expected 95 on): restoring"; the report
                reads 0F:95 N; pp_restores 1; one more planned restart: no
                restoring line, pp_restores 0 (the guard is quiet on an
                intact chip), the bring-up under 1500 ms
  end           the chip ready, 0F:95 N, the restarts counted

usage: obd_bringup_bench.py [--dut localhost:18081] [--restarts N] [--skip-pp]
  run on the PC with the DUT through test.ps1's ssh tunnel (the obdbringup
  stage). Needs: the branch firmware on the DUT, the chip up, the /ws/obd
  bridge (br_obd, the canonical bench settings). 5 planned restarts with the
  defaults, about 3 min; the chip's PP 0F takes 3 EEPROM writes (the fault,
  the restore's SV + ON); --skip-pp drops that leg. AutoPID is left as found.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import wsmin  # noqa: E402  (the /ws/obd bridge)

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT_HP = arg("--dut", "localhost:18081")
DUT = "http://" + DUT_HP
RESTARTS = int(arg("--restarts", "3"))
SKIP_PP = "--skip-pp" in ARGS
BRINGUP_MAX_MS = 1500
CHECKS = []
PLANNED = 0
ESC = chr(27)


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""), flush=True)
    return bool(ok)


def http(base, path, method="GET", body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def chip():
    st, oc = http(DUT, "/api/obd_chip")
    return oc if st == 200 and isinstance(oc, dict) else {}


def guard():
    return chip().get("eeprom_guard", {})


def wait_dut(limit):
    """/api/info answers again after a planned restart"""
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < limit:
        st, _ = http(DUT, "/api/info", timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def wait_ready(limit):
    """GET /api/obd_chip once the chip is ready and unclaimed"""
    t0 = time.time()
    oc = {}
    while time.time() - t0 < limit:
        oc = chip()
        if oc.get("ready") and oc.get("claim") == "none":
            return oc
        time.sleep(0.5)
    return oc


def restart():
    """a planned restart; back with the chip ready"""
    global PLANNED
    http(DUT, "/api/logs/ring", "DELETE")
    st, _ = http(DUT, "/api/restart", "POST", {})
    PLANNED += 1
    ok = st == 200 and wait_dut(60)
    oc = wait_ready(40) if ok else {}
    time.sleep(2)  # the bring-up's last lines into the ring
    return ok and bool(oc.get("ready")), oc


def ring_lines():
    """the log ring's obd_chip lines, ANSI colour stripped"""
    st, txt = http(DUT, "/api/logs/ring", timeout=15)
    if st != 200:
        return []
    text = txt if isinstance(txt, str) else json.dumps(txt)
    out = []
    for l in text.splitlines():
        while ESC in l:
            i = l.index(ESC)
            j = l.find("m", i)
            if j < 0:
                l = l[:i]
                break
            l = l[:i] + l[j + 1:]
        if "obd_chip" in l:
            out.append(l.strip())
    return out


def bringup_ms(lines):
    for l in lines:
        m = re.search(r"bring-up done in (\d+) ms", l)
        if m:
            return int(m.group(1))
    return None


def pp0f():
    """PP 0F out of the report's ATPPS: ('95', 'N') or (None, None)"""
    st, rep = http(DUT, "/api/obd_chip/report", timeout=25)
    if st != 200 or not isinstance(rep, dict):
        return None, None
    for it in rep.get("items", []):
        if it.get("cmd") == "ATPPS":
            m = re.search(r"(?:^|\s)0F:([0-9A-Fa-f]{2})\s+([NF])", it.get("resp", ""))
            if m:
                return m.group(1).upper(), m.group(2)
    return None, None


def probe(word):
    """the bare-wire probe, unguarded: the word as typed, the answer line"""
    st, r = http(DUT, "/api/obd_chip/probe", "POST",
                 {"kind": "probe", "baud": 2000000, "seconds": 0, "cmd": word})
    if st != 202:
        return None, "%s %s" % (st, r)
    pr = None
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        st, pr = http(DUT, "/api/obd_chip/probe")
        if st == 200 and isinstance(pr, dict) and pr.get("done") and not pr.get("running"):
            break
    lines = (pr or {}).get("lines", [])
    for ln in lines:
        if ln.get("cmd") == word:
            return ln, [(x.get("cmd"), x.get("prompt"), x.get("text")) for x in lines]
    return None, [(x.get("cmd"), x.get("prompt"), x.get("text")) for x in lines]


class ElmWs:
    """The chip through the /ws/obd bridge, like a browser terminal: one
    command, the bytes up to the prompt."""

    def __init__(self):
        host, _, port = DUT_HP.partition(":")
        self.ws = wsmin.WS(host, int(port or 80), "/ws/obd", timeout=3)
        self.ws.settimeout(0.2)
        self.opcode = wsmin.OP_BIN
        if not self.cmd("", wait_s=1.0):
            self.opcode = wsmin.OP_TEXT

    def cmd(self, text, wait_s=2.0):
        self.ws.pump()
        self.ws.send((text + "\r").encode(), self.opcode)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            frames = self.ws.pump()
            for _, payload in frames:
                buf += payload
            if b">" in buf:
                break
            if not frames:
                time.sleep(0.02)
        return buf.decode(errors="replace").replace("\r", " ").replace("\n", " ").strip()

    def close(self):
        self.ws.close()


def proto_digit(dpn):
    s = dpn.split(">")[0].replace("ATDPN", "").strip()
    s = re.sub(r"\s*\[.*\]$", "", s).strip()
    if len(s) == 2 and s[0] == "A":
        s = s[1:]
    return s


def leg_as_found():
    oc = wait_ready(20)
    check("the chip ready and unclaimed as found", oc.get("ready") and oc.get("claim") == "none",
          json.dumps({k: oc.get(k) for k in ("ready", "claim", "baud")}))
    v, f = pp0f()
    check("the report's ATPPS reads 0F:95 N as found", v == "95" and f == "N", "0F:%s %s" % (v, f))
    st, ap = http(DUT, "/api/settings/autopid")
    enabled = bool(isinstance(ap, dict) and ap.get("enabled"))
    print("%s AutoPID %s (left as found)" % (now(), "on" if enabled else "off"), flush=True)
    return enabled


def leg_warm_start(i, autopid_on):
    label = "restart %d of %d" % (i + 1, RESTARTS)
    e = ElmWs()
    e.cmd("ATE0")
    e.cmd("ATTP7")
    before = proto_digit(e.cmd("ATDPN"))
    e.close()
    check("%s: ATTP7 then ATDPN reads 7 before the restart" % label, before == "7", before)
    ok, oc = restart()
    check("%s: the DUT back with the chip ready" % label, ok, json.dumps({k: oc.get(k) for k in ("ready", "claim")}))
    lines = ring_lines()
    warm = [l for l in lines if "warm start (ATWS)" in l]
    started = [l for l in lines if "started: chip" in l]
    bad = [l for l in lines if "soft reset not confirmed" in l or "chip not ready; hardware reset" in l]
    ms = bringup_ms(lines)
    check("%s: the ring reads 'chip awake; warm start (ATWS)'" % label, len(warm) == 1, warm or lines[:6])
    check("%s: the ring reads 'started: chip ... memory off'" % label,
          len(started) == 1 and "memory off" in started[0], started)
    check("%s: no 'soft reset not confirmed', no reset pulse" % label, not bad, bad)
    check("%s: bring-up done in under %d ms" % (label, BRINGUP_MAX_MS),
          ms is not None and ms < BRINGUP_MAX_MS, "%s ms" % ms)
    g = oc.get("eeprom_guard", {})
    check("%s: memory_off 1 and pp_restores 0 after the boot" % label,
          g.get("memory_off") == 1 and g.get("pp_restores") == 0, json.dumps(g))
    if autopid_on:
        print("%s %s: AutoPID on, the stored-protocol read-back skipped" % (now(), label), flush=True)
    else:
        e = ElmWs()
        e.cmd("ATE0")
        after = proto_digit(e.cmd("ATDPN"))
        e.close()
        check("%s: ATDPN back on the stored protocol (the warm start dropped the session)" % label,
              after not in ("", "7"), after)
    return ms


def leg_pp0f():
    ln, detail = probe("ATPP0FOFF")
    check("the wire probe's ATPP 0F OFF answered OK", ln is not None and ln.get("prompt") and "OK" in str(ln.get("text")), detail)
    wait_ready(20)
    v, f = pp0f()
    check("the report reads 0F:95 F after the fault", v == "95" and f == "F", "0F:%s %s" % (v, f))
    ok, oc = restart()
    check("restart after the fault: the DUT back with the chip ready", ok)
    lines = ring_lines()
    rest = [l for l in lines if "restoring" in l]
    check("the ring reads 'PP 0F reads 95 off (expected 95 on): restoring'",
          len(rest) == 1 and "95 off" in rest[0], rest or lines[:8])
    v, f = pp0f()
    check("the report reads 0F:95 N after the restore", v == "95" and f == "N", "0F:%s %s" % (v, f))
    g = oc.get("eeprom_guard", {})
    check("pp_restores 1 after the restore", g.get("pp_restores") == 1, json.dumps(g))
    ok, oc = restart()
    check("one more restart: the DUT back with the chip ready", ok)
    lines = ring_lines()
    rest = [l for l in lines if "restoring" in l or "ATPPS:" in l]
    ms = bringup_ms(lines)
    g = oc.get("eeprom_guard", {})
    check("the guard quiet on the intact chip: no restoring line, pp_restores 0",
          not rest and g.get("pp_restores") == 0, rest or json.dumps(g))
    check("bring-up done in under %d ms on the intact chip" % BRINGUP_MAX_MS,
          ms is not None and ms < BRINGUP_MAX_MS, "%s ms" % ms)


def main():
    print("%s OBD BRINGUP bench on %s (%d restarts%s)" % (now(), DUT, RESTARTS, ", PP 0F leg skipped" if SKIP_PP else ""), flush=True)
    autopid_on = leg_as_found()
    times = []
    for i in range(RESTARTS):
        ms = leg_warm_start(i, autopid_on)
        if ms is not None:
            times.append(ms)
    if times:
        print("%s bring-up times: %s ms" % (now(), " ".join(str(t) for t in times)), flush=True)
    if not SKIP_PP:
        leg_pp0f()
    oc = wait_ready(20)
    v, f = pp0f()
    check("the chip ready at the end, 0F:95 N", oc.get("ready") and v == "95" and f == "N", "0F:%s %s" % (v, f))
    print("%s planned restarts: %d" % (now(), PLANNED), flush=True)
    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("OBD BRINGUP %s (%d checks, %d failed, %d planned restarts)" % (
        "PASS" if not fails else "FAIL", len(CHECKS), len(fails), PLANNED))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
