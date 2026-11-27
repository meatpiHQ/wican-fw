#!/usr/bin/env python3
"""A request two ECUs answer  ->  AUTOPID TWO ECUS PASS / FAIL

A row that sets no request header of its own goes out on the functional id,
and every ECU that knows the identifier answers (the published Renault Zoe
and Kangoo profiles and the Jaguar I-Pace one ask their odometer so). Which
answer the device reads:
  a custom or vehicle row   the first ECU, in arrival order, whose reply
                            answers the request. The legacy firmware
                            evaluated such a row over every line in arrival
                            order, so a published expression counts into
                            the answer that came first.
  a standard row            the lowest id (7E8, the engine ECU).
Until 2026-11-27 v6 read the lowest id for every row. An ECU with a lower id
that holds no mileage of its own and answers `FF FF FF` behind the real
answer became the odometer: 16777215 km (a field report of 2026-11-24, "about
half of the time, never on 4.5x"), and an ECU with a lower id that refused
left the row without a value.

Two ECUs are played on the PCAN (`actors/pcan_two_ecus.py`) for one
identifier, 22 2006 (the Renault odometer): A on 7EC holds 123456 km, B on
7EA is the other one. The ECU simulator is told to stay silent for the
identifier and stays on the bus as the ACK source and for the standard rows.

Legs:
  L0 preflight: the device answers, no app or diagnostic tool holds the
     chip, the table is saved, the simulator is silent for the identifier
     (a shot with no ECU played reads NO DATA).
  L1 the report: A answers first, B `FF FF FF` 15 ms later. A vehicle row
     and a custom row through the test route: both ECUs' lines in the chip's
     reply, the value A's, every shot.
  L2 a refusal: B answers `7F 22 31`, behind A and ahead of it: A's value.
  L3 the order is the rule, not the id: B first with a mileage of its own
     reads B's, A first reads A's.
  L4 a standard row keeps the lowest id: B's value though A answered first.
  L5 29-bit ids: the request on 18DB33F1, A on 18DAF1DA, B on 18DAF10A with
     `FF FF FF` behind it: A's value.
  L6 the poller: one vehicle row put into the unit's table and watched for
     --watch seconds: every value published is A's, the row is re-read, and
     both ECUs answered every request of the watch.
  L7 restore: the table as found, the simulator's own answer back, `0100`
     answered by the simulator.

usage: autopid_two_ecus_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS1]
          [--shots 8] [--watch 40] [--out DIR]
  Runs on the PC (python-can + PCAN); the DUT through an ssh tunnel
  (`ssh -N -L 18081:<dut>:80 rpi001`) or by address.
Needs: the main firmware on the DUT on the bench AP, PCAN_USBBUS1 on the DUT
bus, ECU sim v2 on the bus, the supply above the sleep voltage. ~2 min, no
restart, no setting changed (one row added to the table and taken out).
The poll leg's row comes round with the unit's whole table, so its watch
is long enough for three readings beside the 29 rows of the bench unit.
"""
import copy
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import sim_net  # noqa: E402

ACTOR = os.path.join(HERE, "..", "actors", "pcan_two_ecus.py")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DID = "2006"
CMD = "22" + DID
EXPR = "[B4:B6]"
A_KM = 0x01E240                 # 123456: ECU A's mileage
B_KM = 0x00ABCD                 # 43981: ECU B's, where it holds one
NONE_KM = 0xFFFFFF              # 16777215: an ECU that holds none
# the functional request of the car's protocol: the row names its header and
# clears the receive filter, so a row before it cannot lend it another ECU
INIT_11 = "ATSP6;ATSH7DF;ATCRA;"
INIT_29 = "ATSP7;ATCP18;ATSHDB33F1;ATCRA;"
ROW_NAME = "TWO_ECUS_BENCH"
PARAM_NAME = "TWO_ECUS_KM"


def http(base, method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def get_json(base, path):
    st, body = http(base, "GET", path)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


class Actor:
    """pcan_two_ecus.py for one leg: its lines to the run's log, its
    requests and answers counted as they are printed."""

    def __init__(self, pcan, log, **opts):
        args = [sys.executable, "-u", ACTOR, "900", "--pcan", pcan,
                "--did", DID]
        for k, v in opts.items():
            flag = "--" + k.replace("_", "-")
            if v is True:
                args.append(flag)
            elif v not in (None, False):
                args += [flag, str(v)]
        self.log = log
        self.req = 0
        self.rsp = {}
        self.ready = threading.Event()
        self.p = subprocess.Popen(args, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _read(self):
        for line in self.p.stdout:
            line = line.rstrip()
            self.log.write(line + "\n")
            self.log.flush()
            if line == "READY":
                self.ready.set()
            elif line.startswith("REQ"):
                self.req += 1
            elif line.startswith("RSP"):
                arb = line.split()[2]
                self.rsp[arb] = self.rsp.get(arb, 0) + 1

    def up(self):
        return self.ready.wait(8) and self.p.poll() is None

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        time.sleep(0.8)         # the PCAN channel free for the next one


def shot(base, typ, init, cmd=CMD):
    body = {"cmd": cmd, "init": init, "expression": EXPR}
    if typ:
        body["type"] = typ
    st, text = http(base, "POST", "/api/autopid/test", body, timeout=40)
    try:
        t = json.loads(text)
    except ValueError:
        t = {"error": text[:120]}
    raw = str(t.get("raw", "")).replace("\r", "|").replace("\n", "|").strip("|")
    return st, t, " ".join(raw.split())


def shots_read(c, base, name, typ, init, n, want, lines):
    """@p n shots: every one answers, prints every line of @p lines and
    decodes to @p want."""
    values, raws, bad = {}, {}, 0
    for _ in range(n):
        st, t, raw = shot(base, typ, init)
        v = t.get("value")
        key = "none" if v is None else "%d" % v
        values[key] = values.get(key, 0) + 1
        raws[raw] = raws.get(raw, 0) + 1
        if st != 200 or not t.get("ok") or v != want or \
                any(x not in raw for x in lines):
            bad += 1
        time.sleep(0.25)
    top = sorted(raws.items(), key=lambda kv: -kv[1])[0][0] if raws else ""
    return c.add("%s == %d" % (name, want), bad == 0,
                 "%d shots: %s | %s" % (n, ", ".join(
                     "%s x%d" % kv for kv in sorted(values.items())), top))


def sim_silence(sim, on):
    if on:
        st, body = http(sim, "POST", "/api/ecu/errors/22/" + DID,
                        {"enabled": True, "response_type": "no_response",
                         "trigger": "continuous"})
        return st == 200 and '"ok":true' in body.replace(" ", "")
    st, _ = http(sim, "DELETE", "/api/ecu/errors/22/" + DID)
    return st == 200


def frame(arb, km):
    return "%X 06 62 20 06 %02X %02X %02X" % (arb, km >> 16, (km >> 8) & 0xFF,
                                              km & 0xFF)


def frame29(last, km):
    return "18 DA F1 %02X 06 62 20 06 %02X %02X %02X" % (
        last, km >> 16, (km >> 8) & 0xFF, km & 0xFF)


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    pcan = opt("--pcan", os.environ.get("WICAN_PCAN_DUT", "PCAN_USBBUS1"))
    n = int(opt("--shots", 8))
    watch_s = float(opt("--watch", 40))
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_two_ecus_" + ts))
    os.makedirs(out, exist_ok=True)
    sim = sim_net.sim_url()
    print("AUTOPID TWO ECUS bench: dut=%s pcan=%s sim=%s shots=%d watch=%.0f s "
          "out=%s" % (dut, pcan, sim, n, watch_s, out), flush=True)
    c = Checks()
    log = open(os.path.join(out, "actor.log"), "w")
    actor = None
    saved_cfg = None
    table_changed = False
    silenced = False

    def play(title, **opts):
        nonlocal actor
        if actor is not None:
            actor.stop()
        log.write("---- %s\n" % title)
        actor = Actor(pcan, log, **opts)
        return actor.up()

    try:
        # ---- L0 preflight ------------------------------------------------
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s %s" % (st, (info or {}).get("fw_version"),
                                   (info or {}).get("fw_build"))):
            raise SystemExit(1)
        st, ap = get_json(base, "/api/autopid")
        stats = (ap or {}).get("stats", {})
        c.add("no app or diagnostic hold on the chip",
              not stats.get("paused_client") and not stats.get("paused_diag"),
              json.dumps({k: stats.get(k) for k in
                          ("running", "paused_client", "paused_diag",
                           "paused_voltage", "paused_bus")}))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("table saved", st == 200 and saved_cfg is not None and
              not any(p.get("name") == ROW_NAME
                      for p in (saved_cfg or {}).get("pids", [])),
              "%d pids, %d filters" % (len((saved_cfg or {}).get("pids", [])),
                                       len((saved_cfg or {}).get("filters", []))))
        with open(os.path.join(out, "saved_config.json"), "w") as f:
            json.dump(saved_cfg, f)
        silenced = True         # before the write: the restore must run
        c.add("the simulator stays silent for 22 %s" % DID,
              sim_silence(sim, True), sim)
        st, t, raw = shot(base, "specific", INIT_11)
        c.add("nobody but the played ECUs answers 22 %s" % DID,
              st == 200 and "NO DATA" in raw and t.get("value") is None,
              "%s raw=%r" % (st, raw[:60]))
        if c.failed():
            raise SystemExit(1)

        # ---- L1 the report -----------------------------------------------
        print("L1 the report: the real answer first, an ECU with a lower id "
              "and no mileage behind it")
        both = [frame(0x7EC, A_KM), frame(0x7EA, NONE_KM)]
        c.add("two ECUs on the PCAN (A 7EC first, B 7EA `FF FF FF` +15 ms)",
              play("L1", a_data="%06X" % A_KM, b_data="%06X" % NONE_KM))
        shots_read(c, base, "a vehicle row reads the first answer", "specific",
                   INIT_11, n, A_KM, both)
        shots_read(c, base, "a custom row reads the first answer", "custom",
                   INIT_11, n, A_KM, both)

        # ---- L2 a refusal ------------------------------------------------
        print("L2 an ECU that refuses is passed over")
        c.add("B refuses behind A",
              play("L2 behind", a_data="%06X" % A_KM, b_data="nrc:31"))
        shots_read(c, base, "a refusal behind the answer", "specific", INIT_11,
                   n, A_KM, [frame(0x7EC, A_KM), "7EA 03 7F 22 31"])
        c.add("B refuses ahead of A",
              play("L2 ahead", a_data="%06X" % A_KM, b_data="nrc:31",
                   b_first=True))
        shots_read(c, base, "a refusal ahead of the answer", "specific",
                   INIT_11, n, A_KM, [frame(0x7EC, A_KM), "7EA 03 7F 22 31"])

        # ---- L3 the order, not the id -------------------------------------
        print("L3 the order is the rule, not the id")
        two = [frame(0x7EC, A_KM), frame(0x7EA, B_KM)]
        c.add("B (the lower id) answers first with a mileage of its own",
              play("L3 b first", a_data="%06X" % A_KM, b_data="%06X" % B_KM,
                   b_first=True))
        shots_read(c, base, "the lower id first: its value", "specific",
                   INIT_11, n, B_KM, two)
        c.add("A answers first",
              play("L3 a first", a_data="%06X" % A_KM, b_data="%06X" % B_KM))
        shots_read(c, base, "the higher id first: its value", "specific",
                   INIT_11, n, A_KM, two)

        # ---- L4 a standard row --------------------------------------------
        print("L4 a standard row keeps the lowest id (A still answers first)")
        shots_read(c, base, "a standard row reads the lowest id", "std",
                   INIT_11, n, B_KM, two)

        # ---- L5 29-bit ids ------------------------------------------------
        print("L5 29-bit ids: 18DB33F1, answered on 18DAF1DA and 18DAF10A")
        c.add("two ECUs with 29-bit ids (A 18DAF1DA first, B 18DAF10A "
              "`FF FF FF` +15 ms)",
              play("L5", req="0x18DB33F1", a_id="0x18DAF1DA",
                   b_id="0x18DAF10A", a_data="%06X" % A_KM,
                   b_data="%06X" % NONE_KM))
        shots_read(c, base, "29-bit ids: a vehicle row reads the first answer",
                   "specific", INIT_29, n, A_KM,
                   [frame29(0xDA, A_KM), frame29(0x0A, NONE_KM)])

        # ---- L6 the poller ------------------------------------------------
        print("L6 the poller: one vehicle row in the unit's table, %.0f s"
              % watch_s)
        c.add("two ECUs on the PCAN again (A 7EC first, B 7EA `FF FF FF`)",
              play("L6", a_data="%06X" % A_KM, b_data="%06X" % NONE_KM))
        cfg = copy.deepcopy(saved_cfg)
        group = (cfg["pids"][0].get("group") if cfg.get("pids") else None) \
            or "default"
        cfg.setdefault("pids", []).append({
            "cmd": CMD, "group": group, "init": INIT_11, "name": ROW_NAME,
            "period_ms": 1000, "type": "specific",
            "parameters": [{"class": "distance", "expression": EXPR,
                            "name": PARAM_NAME, "unit": "km"}]})
        table_changed = True    # before the write: the restore must run
        st, body = http(base, "PUT", "/api/autopid/config", cfg)
        c.add("the row is in the table", st == 200,
              "%s %s" % (st, body.strip()[:100]))
        seen, stamps = {}, set()
        first_at = None
        req0 = rsp0 = None
        t_end = time.time() + 60
        while time.time() < t_end:
            st, ap = get_json(base, "/api/autopid")
            for p in (ap or {}).get("params", []):
                if p.get("name") != PARAM_NAME or p.get("ts_us") is None or \
                        p.get("value") is None:
                    continue
                if first_at is None:
                    first_at = time.time()
                    req0, rsp0 = actor.req, dict(actor.rsp)
                    t_end = first_at + watch_s
                if p["ts_us"] not in stamps:
                    stamps.add(p["ts_us"])
                    key = "%d" % p["value"]
                    seen[key] = seen.get(key, 0) + 1
            time.sleep(0.4)
        time.sleep(1.0)
        d_req = actor.req - (req0 or 0)
        d_a = actor.rsp.get("7EC", 0) - (rsp0 or {}).get("7EC", 0)
        d_b = actor.rsp.get("7EA", 0) - (rsp0 or {}).get("7EA", 0)
        c.add("the poller publishes the row", first_at is not None,
              json.dumps(seen))
        c.add("every value the poller published is the first answer's (%d)"
              % A_KM, bool(seen) and list(seen) == ["%d" % A_KM],
              json.dumps(seen))
        # the row comes round with the unit's whole table (30 rows on the
        # bench unit: about every 8 s), not at its own period
        c.add("the row keeps being re-read", len(stamps) >= 3,
              "%d readings seen in %.0f s" % (len(stamps), watch_s))
        c.add("both ECUs answered every request of the watch",
              d_req >= 3 and d_a == d_req and d_b == d_req,
              "requests %d, A %d, B %d" % (d_req, d_a, d_b))
    finally:
        # ---- L7 restore ---------------------------------------------------
        print("L7 restore")
        if actor is not None:
            actor.stop()
        log.close()
        if table_changed and saved_cfg is not None:
            st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
            st2, now = get_json(base, "/api/autopid/config")
            c.add("the table as found", st == 200 and st2 == 200 and
                  [p.get("name") for p in (now or {}).get("pids", [])] ==
                  [p.get("name") for p in saved_cfg.get("pids", [])],
                  "%s %s; %d pids" % (st, body.strip()[:60],
                                      len((now or {}).get("pids", []))))
        if silenced:
            c.add("the simulator answers 22 %s again" % DID,
                  sim_silence(sim, False))
        if saved_cfg is not None:
            st, t, raw = shot(base, "", "", cmd="0100")
            c.add("the chip is back on the baseline (0100 answered by the "
                  "simulator)", st == 200 and t.get("ok") and
                  "064100" in raw.replace(" ", ""), "raw=%r" % raw[:40])

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID TWO ECUS FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID TWO ECUS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
