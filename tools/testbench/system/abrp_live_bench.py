#!/usr/bin/env python3
"""ABRP live bench: the data_destinations ABRP rows against the REAL Iternio
telemetry API (PC orchestrator, the DUT through an ssh tunnel to rpi001).

The mock Iternio leg of data_destinations_bench.py proves the request shape;
this bench proves the service accepts it: DNS, TLS through the built-in CA
bundle, the api_key as a query parameter and as the Authorization header, a
wrong key refused, Iternio's verdict read from the body, the Test button, the
backoff on refusals, and (with a user token) Iternio's own get_telemetry as
the witness that the points arrived. Verdict line: ABRP LIVE PASS / FAIL, or
ABRP LIVE PARTIAL PASS when no user token is configured (then only the
refusal path runs: Iternio answers HTTP 401 "Unauthorized Token" to a valid
key with an unknown token, which still proves everything up to the verdict).

Credentials come from a JSON file OUTSIDE the tracked tree (default
internal/abrp_credentials.json, gitignored): {"api_key": "...",
"user_token": "..."} (the token from the ABRP app: the vehicle's live data
setup, Generic). Nothing here prints either value, the DUT never returns them
(has_token / has_api_key only), and the saved originals hold the DUT's
redacted settings. The bench prints no coordinates: get_telemetry's lat/lon/
elevation values are withheld.

The bench subnet has no internet path by itself (the Pi's eth-bench profile is
never-default on purpose): for the run the Pi gets HOST routes to Iternio's
addresses via the LAN router (`ip route replace <ip>/32 via <gw> dev <uplink>`,
refreshed while the run lasts, deleted at the end); --no-routes skips that
when the DUT has its own internet (the ESPNetLink as the default route).

Legs:
  L0 credentials + the key against Iternio from the PC (get_carmodels_list)
  L1 the Pi's egress for the DUT (host routes), the DUT's clock, originals
  L2 configure: originals + abrp (api_key in the query), abrph (Authorization:
     APIKEY), abrpx (a wrong key, staged at 2 s: the 5 s floor applies);
     one planned restart; /api/destinations/abrp names the fields the car
     lacks (soc from SOC, power from HV_W)
  L3 observe (--observe s): counters per row; full mode = accepted at the
     row's cadence (every 5 s within 2 s) without a failure; negative mode =
     401 refusals with the text and the backoff
  L4 the Test button on each row (POST /api/destinations/test), back to back:
     the device-wide pacer (ABRP requests start >= 2.5 s apart) holds the
     Tests as it holds the rows (`abrp_pacer` in the status document)
  L5 the witness (full mode): get_telemetry knows the token's vehicle (status
     ok with a telemetry object); its latest point and `is_connected` are
     reported, not judged: Iternio's pipeline runs minutes behind and keeps no
     new point for an unmoved car, the acceptance is the `status: ok` of L3
  L6 restore (originals + one planned restart), routes removed, the closing
     checks (own E lines, stack headroom, no unexpected reset)

  python abrp_live_bench.py --dut localhost:18081 --pi rpi001 [--observe 120]
      [--creds internal/abrp_credentials.json] [--car-model hyundai:ioniq5:22:74]
      [--pi-gw 192.168.8.1 --pi-uplink eth0 | --no-routes] [--originals file]

Rate: Iternio allows 2 requests per second per api_key; two valid rows every
5 s plus three Test deliveries stay far below it.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import pcbench  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ITERNIO = "https://api.iternio.com/1/tlm"
ITERNIO_HOST = "api.iternio.com"
PLACEHOLDER_TOKEN = "bench-no-token"
WRONG_KEY = "bench-wrong-key"
PERIOD_S = 5
BENCH_ROWS = ("abrp", "abrph", "abrpx")
WITHHELD_KEYS = ("lat", "lon", "elevation", "heading")

A = None
run = pcbench.Run("ABRP LIVE")


def note(msg):
    print("  " + msg, flush=True)


# ---- the Pi ---------------------------------------------------------------------------

def pi(cmd, timeout=60):
    """ssh to the Pi; 255 = transport failure, retried twice."""
    r = None
    for _ in range(3):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", A.pi, cmd],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 255:
            return r.returncode, (r.stdout + r.stderr).strip()
        time.sleep(2)
    return 255, (r.stdout + r.stderr).strip()


class Routes:
    """Host routes on the Pi to Iternio's addresses, for the run only."""

    def __init__(self):
        self.added = set()
        self.preexisting = set()

    def resolve(self):
        rc, out = pi(f"getent ahostsv4 {ITERNIO_HOST} | awk '{{print $1}}' | sort -u")
        return sorted(ip for ip in out.split() if ip.count(".") == 3) if rc == 0 else []

    def ensure(self):
        """Resolve on the Pi (its resolver is what the DUT's DNS forwards to) and
        add a host route for every address that has none. Returns the new ones."""
        new = []
        for ip in self.resolve():
            if ip in self.added or ip in self.preexisting:
                continue
            rc, out = pi(f"ip -4 route show {ip}/32")
            if rc == 0 and out.strip():
                self.preexisting.add(ip)
                continue
            rc, out = pi(f"sudo -n ip route replace {ip}/32 via {A.pi_gw} dev {A.pi_uplink}")
            if rc == 0:
                self.added.add(ip)
                new.append(ip)
            else:
                note(f"route for {ip} refused: {out[:120]}")
        return new

    def drop(self):
        for ip in sorted(self.added):
            pi(f"sudo -n ip route del {ip}/32 via {A.pi_gw} dev {A.pi_uplink}")
        self.added.clear()


# ---- Iternio from the PC ----------------------------------------------------------------

def iternio(path, params):
    """(http status, json or text). The URL carries the key: never printed."""
    url = f"{ITERNIO}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            code, text = r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, type(e).__name__
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text[:200]


def withhold(d):
    """The object with every position value replaced, at any depth (no
    coordinates in a log, ever: get_telemetry nests them under `telemetry`)."""
    if isinstance(d, dict):
        return {k: ("withheld" if k in WITHHELD_KEYS else withhold(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [withhold(v) for v in d]
    return d


def parse_when(obj):
    """Iternio's time of a point: `utc` (epoch seconds) or `timestamp`
    ('2026-10-13 05:10:28+00:00'). None when neither parses."""
    utc = obj.get("utc")
    if isinstance(utc, (int, float)):
        return float(utc)
    ts = obj.get("timestamp")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


# ---- rows -------------------------------------------------------------------------------

def row(**kw):
    base = {"enabled": True, "period_s": PERIOD_S, "auth": "none", "auth_token": "", "auth_name": "",
            "basic_username": "", "basic_password": "", "api_key": "", "query": "", "cert_set": "",
            "car_model": "", "retain": True, "full_first": True, "url": ""}
    return base | kw


def bench_rows(api_key, token, car_model):
    return [
        row(name="abrp", type="abrp", auth="api_key_query", auth_name="api_key",
            auth_token=token, api_key=api_key, car_model=car_model),
        row(name="abrph", type="abrp", auth="api_key_header", auth_name="Authorization",
            auth_token=token, api_key=api_key, car_model=car_model),
        # staged at 2 s: the firmware applies the 5 s floor (checked in L2)
        row(name="abrpx", type="abrp", auth="api_key_query", auth_name="api_key",
            auth_token=token, api_key=WRONG_KEY, car_model=car_model, period_s=2),
    ]


def dest_status(dut):
    st = dut.get("/api/destinations")
    return {d["name"]: d for d in st.get("destinations", [])}, st


def brief(d):
    return {k: d.get(k) for k in ("success", "fail", "last_status", "last_error",
                                  "consecutive_failures", "backoff_s")}


def wait_running(dut, secs=60):
    t0 = time.time()
    while time.time() - t0 < secs:
        code, st = dut.api("/api/destinations")
        if code == 200 and isinstance(st, dict) and st.get("running") and st.get("network"):
            return True
        time.sleep(2)
    return False


# ---- legs -------------------------------------------------------------------------------

def leg0_credentials():
    print("== L0 credentials + the key against Iternio (from the PC)", flush=True)
    try:
        with open(A.creds, encoding="utf-8") as f:
            creds = json.load(f)
    except (OSError, ValueError) as e:
        raise pcbench.Bench(f"credentials file {A.creds}: {type(e).__name__}")
    api_key = str(creds.get("api_key") or "").strip()
    token = str(creds.get("user_token") or "").strip()
    if not api_key:
        raise pcbench.Bench(f"no api_key in {A.creds}")
    full = bool(token) and token != PLACEHOLDER_TOKEN
    note(f"api_key present ({len(api_key)} chars); user token "
         + ("present: FULL mode" if full else "absent: NEGATIVE mode (the refusal path only)"))
    code, r = iternio("get_carmodels_list", {"api_key": api_key})
    models = r.get("result", []) if isinstance(r, dict) else []
    run.check("L0: Iternio accepts the api_key (get_carmodels_list status ok)",
              code == 200 and isinstance(r, dict) and r.get("status") == "ok" and len(models) > 100,
              f"http {code} {str(r)[:80] if not models else ''}models={len(models)}")
    run.metric("iternio_models", len(models))
    if A.car_model:
        codes = {c for it in models for c in it.values()}
        run.check(f"L0: --car-model {A.car_model} is an Iternio typecode", A.car_model in codes)
    time.sleep(0.6)   # 2 requests per second per key
    return api_key, (token if full else PLACEHOLDER_TOKEN), full


def leg1_rig(dut, routes):
    print("== L1 the Pi's egress for the DUT, the DUT's clock, originals", flush=True)
    if A.no_routes:
        note("--no-routes: the DUT's own internet path is used")
    else:
        new = routes.ensure()
        ips = sorted(routes.added | routes.preexisting)
        run.check("L1: host routes to Iternio on the Pi", len(ips) >= 1,
                  f"{len(ips)} addresses, {len(new)} added, {len(routes.preexisting)} pre-existing")
    info = dut.get("/api/info")
    note(f"DUT {info.get('device_id')} {info.get('fw_version')}")
    names, st = dest_status(dut)
    run.check("L1: firmware has /api/destinations", isinstance(st, dict) and "destinations" in st)
    run.check("L1: the DUT has a network", st.get("network") is True, str(st.get("network")))
    code, rtc = dut.api("/api/rtc")
    offset = None
    if code == 200 and isinstance(rtc, dict) and isinstance(rtc.get("time"), str):
        try:
            t = datetime.strptime(rtc["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            offset = round(t.timestamp() - time.time(), 1)
        except ValueError:
            pass
    run.check("L1: the DUT's clock within 120 s of the PC (ABRP's utc is the point's time)",
              offset is not None and abs(offset) <= 120,
              f"offset {offset} s, valid={rtc.get('valid') if isinstance(rtc, dict) else '?'}")
    if offset is not None:
        run.metric("dut_clock_offset_s", offset)
    orig = dut.settings("data_destinations")
    if A.originals:
        orig = json.load(open(A.originals, encoding="utf-8"))
        note(f"originals taken from {A.originals}")
    keep = os.path.join(os.environ.get("TEMP", "."), "abrp_live_originals.json")
    json.dump(orig, open(keep, "w", encoding="utf-8"), indent=1)
    note(f"originals: {len(orig.get('destinations', []))} rows, enabled={orig.get('enabled')} (saved to {keep})")
    return orig


def leg2_configure(dut, orig, api_key, token):
    print("== L2 configure the three ABRP rows (one planned restart)", flush=True)
    kept = [d for d in orig.get("destinations", []) if d.get("name") not in BENCH_ROWS]
    if len(kept) + len(BENCH_ROWS) > 8:
        dropped = kept[8 - len(BENCH_ROWS):]
        kept = kept[:8 - len(BENCH_ROWS)]
        note("table full: " + ", ".join(d["name"] for d in dropped) + " left out for the run")
    rows = kept + bench_rows(api_key, token, A.car_model)
    t_up = dut.restart({"data_destinations": {"enabled": True, "destinations": rows}})
    note(f"DUT back {time.strftime('%H:%M:%S', time.localtime(t_up))}")
    names, st = dest_status(dut)
    run.check("L2: the three rows are applied", all(n in names for n in BENCH_ROWS), ",".join(names))
    run.check("L2: the ABRP period floor: abrpx staged at 2 s runs at 5 s, abrp keeps 5",
              names.get("abrpx", {}).get("period_s") == PERIOD_S and names.get("abrp", {}).get("period_s") == PERIOD_S,
              json.dumps({n: names.get(n, {}).get("period_s") for n in BENCH_ROWS}))
    code, cov = dut.api("/api/destinations/abrp")
    cov = cov if isinstance(cov, dict) else {}
    missing = [m.get("field") for m in cov.get("missing", []) if isinstance(m, dict)]
    run.check("L2: /api/destinations/abrp names the fields the bench car lacks (soc from SOC, power from HV_W)",
              code == 200 and "soc" in missing and "power" in missing
              and any(m.get("field") == "soc" and m.get("parameter") == "SOC" for m in cov.get("missing", []))
              and cov.get("period_floor_s") == PERIOD_S and cov.get("request_gap_ms") == 2500,
              f"http {code} present={cov.get('present')} missing={missing}")
    run.check("L2: secrets never come back (has_token / has_api_key only)",
              all(names[n].get("has_token") is True and names[n].get("has_api_key") is True
                  and "auth_token" not in names[n] and "api_key" not in names[n] for n in BENCH_ROWS if n in names))
    run.check("L2: poster running with a network after the restart", wait_running(dut))


def leg3_observe(dut, routes, full):
    print(f"== L3 observe {A.observe} s", flush=True)
    start, _ = dest_status(dut)
    t0 = time.time()
    last_route_check = t0
    names = {}
    top = {}
    while time.time() - t0 < A.observe:
        time.sleep(PERIOD_S)
        names, top = dest_status(dut)
        line = "  ".join(f"{n}: ok {names[n]['success']} fail {names[n]['fail']} http {names[n]['last_status']}"
                         f" backoff {names[n]['backoff_s']}" for n in BENCH_ROWS if n in names)
        print(f"  t={time.time() - t0:4.0f}s  {line}", flush=True)
        if not A.no_routes and time.time() - last_route_check >= 20:
            last_route_check = time.time()
            new = routes.ensure()
            if new:
                note("new Iternio address routed: " + ", ".join(new))
    window = time.time() - t0
    ab, abh, abx = (names.get(n, {}) for n in BENCH_ROWS)
    for n in BENCH_ROWS:
        run.metric(f"{n}_success", names.get(n, {}).get("success", -1))
        run.metric(f"{n}_fail", names.get(n, {}).get("fail", -1))
    if full:
        # the cadence over the window (counts as deltas: the first laps ran
        # before it): a 5 s row must send about every 5 s, not every 5 s plus
        # its own request time (the ~1.8 s TLS round trip to Iternio), which
        # is what booking the next due time from the END of a delivery gave
        # (12 points in 90 s on 2026-10-13, one every 7.5 s)
        for n, d in (("abrp", ab), ("abrph", abh)):
            got = d.get("success", 0) - start.get(n, {}).get("success", 0)
            cadence = round(window / got, 1) if got else None
            run.metric(f"{n}_cadence_s", cadence if cadence is not None else -1)
            run.check(f"L3: {n} accepted by Iternio at the row's cadence (every {PERIOD_S} s, within 2 s; 0 fail, HTTP 200)",
                      got >= 2 and cadence is not None and cadence <= PERIOD_S + 2 and d.get("fail", 0) == 0
                      and d.get("last_status") == 200 and d.get("consecutive_failures") == 0
                      and d.get("backoff_s") == 0 and d.get("last_error") == "",
                      f"{got} points in {window:.0f} s, one every {cadence} s; " + json.dumps(brief(d)))
    else:
        for n, d in (("abrp", ab), ("abrph", abh)):
            run.check(f"L3: {n} refused by Iternio as HTTP 401 Unauthorized Token (the key was accepted)",
                      d.get("success", 0) == 0 and d.get("fail", 0) >= 3 and d.get("last_status") == 401
                      and d.get("last_error") == 'abrp: error "Unauthorized Token"', json.dumps(brief(d)))
    run.check("L3: abrpx (a wrong key) refused as HTTP 401 Unauthorized Key",
              abx.get("success", 0) == 0 and abx.get("fail", 0) >= 3 and abx.get("last_status") == 401
              and abx.get("last_error") == 'abrp: error "Unauthorized Key"', json.dumps(brief(abx)))
    # refusals back off (3 consecutive -> 10 s, doubling, capped at 60 s for a 5 s period):
    # over the window the wrong-key row attempts far fewer times than the laps
    run.check("L3: a refused row backs off (>= 3 consecutive failures, backoff >= 10 s, fewer attempts than laps)",
              abx.get("consecutive_failures", 0) >= 3 and abx.get("backoff_s", 0) >= 10
              and abx.get("fail", 0) <= A.observe // PERIOD_S - 1, json.dumps(brief(abx)))
    # the device-wide pacer: with two valid rows due together, the second
    # waits for the gap on most laps (requests start >= 2.5 s apart); the
    # wrong-key row behind them must still get its turns (a lap-skipping
    # pacer starved it for good on 2026-10-13)
    pace = top.get("abrp_pacer") or {}
    run.metric("abrp_pacer_waits", pace.get("waits", -1))
    run.check("L3: the ABRP pacer made due rows wait for the 2.5 s gap (waits > 0)",
              pace.get("gap_ms") == 2500 and pace.get("waits", 0) > 0, json.dumps(pace))
    return pace


def leg4_test_button(dut, full, pace0):
    print("== L4 the Test button (POST /api/destinations/test), back to back", flush=True)
    t0 = time.time()
    for n in BENCH_ROWS:
        code, r = dut.api("/api/destinations/test", "POST", {"name": n}, timeout=30)
        r = r if isinstance(r, dict) else {}
        want_ok = full and n != "abrpx"
        want_err = "" if want_ok else ('abrp: error "Unauthorized Key"' if n == "abrpx"
                                       else 'abrp: error "Unauthorized Token"')
        run.check(f"L4: test {n}: " + ("delivered (ok, HTTP 200)" if want_ok else f"refused ({want_err})"),
                  code == 200 and r.get("ok") is want_ok and r.get("status") == (200 if want_ok else 401)
                  and (r.get("error") or "") == want_err, f"http {code} {json.dumps(r)[:160]}")
        if isinstance(r.get("elapsed_ms"), int):
            run.metric(f"test_{n}_elapsed_ms", r["elapsed_ms"])
    wall = time.time() - t0
    _, top = dest_status(dut)
    pace = top.get("abrp_pacer") or {}
    run.metric("tests_back_to_back_s", round(wall, 1))
    run.metric("abrp_pacer_waited_ms", pace.get("waited_ms", -1))
    # three ABRP requests one after the other cannot start closer than 2.5 s:
    # the Test button waits for the gap (waited_ms grows) and the three take
    # at least 2 gaps plus the last request
    run.check("L4: the Test button is paced too (waited_ms grew, three tests >= 2 gaps + a request)",
              pace.get("waited_ms", 0) > pace0.get("waited_ms", 0) and wall >= 2 * 2.5 + 1.0,
              f"waited_ms {pace0.get('waited_ms')} -> {pace.get('waited_ms')}, {wall:.1f} s for three tests")
    code, r = dut.api("/api/destinations/test", "POST", {"name": "nosuchrow"}, timeout=10)
    run.check("L4: test of an unknown row = 404", code == 404, str(code))


def leg5_witness(api_key, token, t_start):
    print("== L5 the witness: Iternio's get_telemetry for this token", flush=True)
    t_ask = time.time()
    code, r = iternio("get_telemetry", {"api_key": api_key, "token": token})
    res = r.get("result") if isinstance(r, dict) else None
    res = res if isinstance(res, dict) else {}
    # the reply (2026-10-13): {"telemetry": {lat, lon, heading, ...}, "timestamp":
    # "2026-10-13 05:10:28+00:00", "name": <the vehicle>, "typecode", "vehicle_id",
    # "user_id", "is_connected", "telemetry_type"}: the latest PROCESSED point
    # (Iternio batches with a delay of a minute or more), so the check is
    # "a point of this run", not "the last one sent"
    point = res.get("telemetry") if isinstance(res.get("telemetry"), dict) else {}
    when = parse_when(point) or parse_when(res)
    lag = round(t_ask - when) if when is not None else None
    run.check("L5: get_telemetry answers status ok with a telemetry object for the vehicle",
              code == 200 and isinstance(r, dict) and r.get("status") == "ok" and bool(point),
              f"http {code} {json.dumps(withhold(res if res else r))[:240]}")
    # Neither the point nor `is_connected` is a real-time witness: Iternio
    # processes points in batches minutes behind, keeps no new point for an
    # unmoved car (three runs, 86 accepted points, the latest point stayed at
    # the first run's 05:11:33 all afternoon on 2026-10-13) and its
    # `is_connected` followed that pipeline (true at 05:26, false at 05:49
    # while 22 points a minute were being accepted). The acceptance IS the
    # `status: ok` Iternio answers to every send (L3); what it shows here is
    # reported, not judged.
    run.metric("witness_is_connected", 1 if res.get("is_connected") is True else 0)
    fresh = when is not None and when >= t_start - 5
    note(f"latest point {datetime.fromtimestamp(when, timezone.utc).strftime('%H:%M:%SZ') if when else None}"
         f" (run start {datetime.fromtimestamp(t_start, timezone.utc).strftime('%H:%M:%SZ')}, lag {lag} s):"
         + (" this run's" if fresh else " an earlier one (the car did not move)"))
    if lag is not None:
        run.metric("witness_lag_s", lag)
    run.metric("witness_point_of_this_run", 1 if fresh else 0)
    note("vehicle: " + str(res.get("name"))[:80] + "; telemetry keys: " + ", ".join(sorted(point.keys()))[:200])


def leg6_restore(dut, orig, routes, st0):
    print("== L6 restore (one planned restart) + the closing checks", flush=True)
    rows = orig.get("destinations", [])
    dut.restart({"data_destinations": {"enabled": orig.get("enabled", True), "destinations": rows}})
    names, st = dest_status(dut)
    run.check("L6: the device runs the original table again",
              sorted(names) == sorted(d["name"] for d in rows) and not any(n in names for n in BENCH_ROWS),
              ",".join(names))
    dut.reset_check(run, st0)
    dut.passing_checks(run)
    client_e = [ln for ln in dut.ring_lines() if ln.startswith("E (") and ") " in ln
                and ln.split(") ", 1)[1].split(":", 1)[0].strip()
                in ("esp-tls", "esp-tls-mbedtls", "HTTP_CLIENT", "esp_http_client", "transport_base",
                    "transport", "esp-x509-crt-bundle", "http_client_manager")]
    run.metric("idf_client_e_lines", len(client_e))
    if client_e:
        note("IDF client E lines: " + " | ".join(client_e[-3:])[:400])
    if not A.no_routes:
        routes.drop()
        note("host routes removed from the Pi")


def main():
    global A
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", required=True, help="DUT host:port (the tunnel, e.g. localhost:18081)")
    ap.add_argument("--pi", default="rpi001", help="ssh host of the bench Pi (the DUT's gateway)")
    ap.add_argument("--pi-gw", default="192.168.8.1", help="the LAN router next to the Pi's uplink")
    ap.add_argument("--pi-uplink", default="eth0", help="the Pi's interface toward that router")
    ap.add_argument("--no-routes", action="store_true", help="add no host routes on the Pi")
    ap.add_argument("--creds", default=os.path.join(REPO, "internal", "abrp_credentials.json"),
                    help="JSON with api_key and user_token (outside the tracked tree)")
    ap.add_argument("--car-model", default="", help="Iternio typecode sent as car_model (optional)")
    ap.add_argument("--observe", type=int, default=120, help="seconds to watch the rows")
    ap.add_argument("--originals", help="data_destinations JSON to restore instead of what the DUT reports")
    A = ap.parse_args()
    t_start = time.time()
    dut = pcbench.Dut(A.dut, own_tags=("data_destinations",), tasks=("data_dest",))
    routes = Routes()
    api_key, token, full = leg0_credentials()
    st0 = dut.get("/api/status")
    dut.as_found()
    orig = None
    try:
        orig = leg1_rig(dut, routes)
        leg2_configure(dut, orig, api_key, token)
        pace0 = leg3_observe(dut, routes, full)
        leg4_test_button(dut, full, pace0)
        if full:
            leg5_witness(api_key, token, t_start)
        else:
            note("L5 skipped: no user token (the witness needs one)")
        leg6_restore(dut, orig, routes, st0)
    except pcbench.Bench as e:
        run.check("rig", False, str(e))
        if orig is not None:
            note("restoring the original table after the rig failure")
            try:
                dut.restart({"data_destinations": {"enabled": orig.get("enabled", True),
                                                   "destinations": orig.get("destinations", [])}})
            except pcbench.Bench as e2:
                note(f"restore failed: {e2}")
        if not A.no_routes:
            routes.drop()
    print(f"  elapsed {time.time() - t_start:.0f} s, {dut.restarts} planned restarts")
    return run.verdict(partial="" if full else "no user token: the refusal path only")


if __name__ == "__main__":
    sys.exit(main())
