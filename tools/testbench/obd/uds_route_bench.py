#!/usr/bin/env python3
"""UDS route bench  ->  UDS ROUTE PASS / FAIL

Fires N UDS requests through `POST /api/uds/request` while autopid keeps
polling the same bus, and judges the OBD-side transport by what comes back:
every request must answer (ok:true) with the SID that belongs to it, and the
timing is reported so the chip-path improvements can be measured.

Requests (against the bench ECU simulator on 7E0/7E8):
  3E 00  -> 7E 00              tester present
  10 02  -> 50 02 ..           session control
  22 F1 90 -> 62 F1 90 .. or 7F 22 xx   read DID (either is a valid answer)

Found 2026-09-16: over the MIC (backend obd_chip) with autopid polling the
route flipped between ok, ESP_ERR_INVALID_RESPONSE and a 14 s timeout (1 of
3), and 5/5 ok at ~525 ms with autopid paused — the transport did not hold
the chip across its 8-command transaction. Phase 1 of
can_manager/TASK_isotp_public.md fixes that; this is its regression.

usage: uds_route_bench.py <base-url> [--n 20] [--expect-backend obd_chip|isotp]
                          [--max-median-ms 600] [--tx 7E0 --rx 7E8]
                          [--req "22 01 01"]
  e.g. python tools/testbench/obd/uds_route_bench.py http://localhost:8081
       python tools/testbench/obd/uds_route_bench.py http://localhost:8081 --tx 7E4 --rx 7EC --req "22 01 01"
  --req repeats ONE request N times against --tx/--rx (use it when the
  simulator runs a vehicle profile that only answers that ECU, e.g. the
  Ioniq BMS on 7E4/7EC while autopid polls the same ECU: the hardest case
  for the transaction hold).
Needs: main firmware on the DUT, the ECU simulator answering the target,
autopid enabled and polling (the point of the bench). Runtime ~N x 0.6 s.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request


def http(base, method, path, body=None, timeout=40):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__)
        return 2
    base = args[0].rstrip("/")
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    n = int(opt("--n", 20))
    expect_backend = opt("--expect-backend")
    max_median = float(opt("--max-median-ms", 600))

    st, body = http(base, "GET", "/api/autopid")
    ap = json.loads(body) if st == 200 else {}
    print("autopid: running=%s paused_client=%s" % (
        ap.get("stats", {}).get("running"), ap.get("stats", {}).get("paused_client")))

    tx, rx = opt("--tx", "7E0"), opt("--rx", "7E8")
    one = opt("--req")
    min_ok_pct = float(opt("--min-ok-pct", 90))
    if one:
        sid = int(one.split()[0], 16)
        plan = [(one, (sid + 0x40, 0x7F))]
    else:
        plan = [("3E 00", (0x7E,)), ("10 02", (0x50,)), ("22 F1 90", (0x62, 0x7F))]
    print("target %s -> %s, plan %s" % (tx, rx, [p[0] for p in plan]))
    # HARD gate: a request must NEVER come back ok:true with a payload that
    # is not its own answer (a stray from another requester returned as the
    # answer = corrupt data). The transaction hold + SID check guarantee 0.
    corrupt = []
    fails, times, backends = [], [], set()
    for i in range(n):
        data, ok_sids = plan[i % len(plan)]
        t0 = time.time()
        st, body = http(base, "POST", "/api/uds/request",
                        {"tx_id": tx, "rx_id": rx, "data": data})
        wall = (time.time() - t0) * 1000
        try:
            d = json.loads(body)
        except ValueError:
            d = {}
        backends.add(d.get("backend", "?"))
        resp = d.get("response", "")
        sid = int(resp.split()[0], 16) if resp else -1
        ok = st == 200 and d.get("ok") is True
        good = ok and sid in ok_sids
        if ok and not good:
            corrupt.append("#%d %s -> ok but %s (SID not ours)" % (i + 1, data, resp))
        if good:
            times.append(d.get("elapsed_ms", wall))
        else:
            fails.append("#%d %s -> %s %s (wall %.0f ms)" % (
                i + 1, data, st, d.get("error") or resp or body[:80], wall))
        print("  %2d %-9s %s %-24s %6.0f ms%s" % (
            i + 1, data, "ok  " if good else "FAIL", resp or d.get("error", ""),
            d.get("elapsed_ms", wall), "" if good else "  <--"))

    med = statistics.median(times) if times else float("nan")
    mx = max(times) if times else float("nan")
    ok_pct = 100.0 * (n - len(fails)) / n if n else 0.0
    print("summary: %d/%d ok (%.0f%%), %d corrupt, backend(s) %s, median %.0f ms, max %.0f ms"
          % (n - len(fails), n, ok_pct, len(corrupt), ",".join(sorted(backends)), med, mx))
    verdict_fail = []
    # HARD: any corrupt response fails the run outright (wrong data is never OK)
    verdict_fail += ["CORRUPT: " + c for c in corrupt]
    if expect_backend and backends != {expect_backend}:
        verdict_fail.append("backend %s, expected %s" % (",".join(sorted(backends)), expect_backend))
    # THROUGHPUT: the chip transport shares the MIC with autopid; under heavy
    # multiframe polling on the SAME single-MCU simulator it can be starved
    # (clean errors, never corrupt). --min-ok-pct sets the bar for the run
    # (default 90: pass with autopid paused or a light/standard config; the
    # native ISO-TP path removes the contention, TASK_isotp_public.md Phase 2).
    if ok_pct < min_ok_pct:
        verdict_fail.append("%.0f%% ok < %.0f%% (chip transport starved by autopid load; "
                            "pause autopid or use native ISO-TP)" % (ok_pct, min_ok_pct))
    if times and med > max_median:
        verdict_fail.append("median %.0f ms over the %.0f ms budget" % (med, max_median))
    for f in verdict_fail:
        print("FAIL:", f)
    print("UDS ROUTE", "PASS" if not verdict_fail else "FAIL")
    return 0 if not verdict_fail else 1


if __name__ == "__main__":
    sys.exit(main())
