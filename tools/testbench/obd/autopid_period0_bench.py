#!/usr/bin/env python3
"""autopid event-pipeline cost at period_ms=0 (max poll rate) — the
deferred Phase-1b measure (TASK_autopid.md). Runs ON rpi001 against the
bench ECU sim feeding the DUT's stored autopid parameters.

Method: snapshot the stored autopid config, drive EVERY parameter's
period_ms to 0 (poll-as-fast-as-possible mode), reboot, then measure a
60 s window: achieved update rate per parameter (ts_us advancement),
HTTP responsiveness under the load (p50 RTT of /api/status), heap drift
(internal + PSRAM min_free), and the health/fault nets. Baseline = the
same window at the stored periods, measured FIRST. Restores everything.

Emits METRIC lines (the test.ps1 scraper convention).

Usage (on rpi001):  python3 autopid_period0_bench.py [dut_ip]
Expected final line: AUTOPID PERIOD0 PASS
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True,
                          text=True).stdout


def find_dut():
    for _ in range(30):
        for dev in ("wint0", "wtest0"):
            for ip in sh(f"ip neigh show dev {dev} | awk '{{print $1}}'"
                         ).split():
                try:
                    if api(ip, "/api/status").get("version"):
                        return ip
                except Exception:
                    pass
        time.sleep(3)
    return None


def api(ip, path, method="GET", body=None, timeout=8):
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def submit_and_wait(ip):
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://{ip}/api/settings/submit",
                                   method="POST"), timeout=8).read()
    except Exception:
        pass
    time.sleep(8)
    return find_dut()


def walk_periods(node, setter=None):
    """Find every 'period_ms' in the config tree; optionally set them."""
    found = []

    if isinstance(node, dict):
        for k, v in node.items():
            if k == "period_ms" and isinstance(v, (int, float)):
                found.append(v)
                if setter is not None:
                    node[k] = setter
            else:
                found += walk_periods(v, setter)
    elif isinstance(node, list):
        for item in node:
            found += walk_periods(item, setter)

    return found


def measure(ip, secs=60, label=""):
    """One measurement window: rates + responsiveness + heap drift."""
    d = api(ip, "/api/autopid")
    ts0 = {p["name"]: p.get("ts_us", 0) for p in d.get("params", [])}
    m0 = api(ip, "/api/status").get("memory", {})
    updates = {k: 0 for k in ts0}
    last = dict(ts0)
    rtts = []
    t0 = time.time()
    hb = t0

    while time.time() - t0 < secs:
        t = time.perf_counter()
        d = api(ip, "/api/autopid")
        rtts.append((time.perf_counter() - t) * 1000)

        for p in d.get("params", []):
            n = p["name"]
            if n in last and p.get("ts_us", 0) > last[n]:
                updates[n] += 1
                last[n] = p["ts_us"]

        if time.time() - hb >= 10:        # liveness during the window
            hb = time.time()
            print(f"  {label} t={hb - t0:.0f}/{secs} s, "
                  f"{sum(updates.values())} updates seen", flush=True)
        time.sleep(0.25)

    m1 = api(ip, "/api/status").get("memory", {})
    rate = {k: v / secs for k, v in updates.items()}
    rtts.sort()
    return {
        "rate_hz": rate,
        "total_hz": sum(rate.values()),
        "rtt_p50": statistics.median(rtts) if rtts else -1,
        "rtt_p95": rtts[int(len(rtts) * 0.95) - 1] if rtts else -1,
        "int_free_delta": (m1.get("internal", {}).get("free", 0) -
                           m0.get("internal", {}).get("free", 0)),
        "psram_free_delta": (m1.get("psram", {}).get("free", 0) -
                             m0.get("psram", {}).get("free", 0)),
    }


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else find_dut()
    print("DUT", ip)
    if not ip or not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    # flags live in the settings component (reboot-to-apply); the PID/
    # group tables live in /api/autopid/config (file-backed, PUT reloads
    # LIVE — first live run 2026-07-26: the original draft walked the
    # settings object, which holds only flags)
    flags_before = api(ip, "/api/settings/autopid")
    for k in ("degraded", "pending_reboot"):
        flags_before.pop(k, None)

    tables_before = api(ip, "/api/autopid/config")
    periods = walk_periods(json.loads(json.dumps(tables_before)))
    print(f"stored periods: {periods}")

    if not periods:
        print("FATAL: no period_ms in the stored autopid tables "
              "(/api/autopid/config) — configure parameters first (the "
              "bench DUT usually has the sim profile loaded)")
        return 1

    # NOTE: the sampling window sees a ~4 Hz observer floor (we poll
    # /api/autopid every 250 ms) — rates are lower bounds per param.

    # ---- baseline at the stored periods ----
    enabled_flip = not flags_before.get("enabled", False)

    if enabled_flip:
        flags = dict(flags_before)
        flags["enabled"] = True
        api(ip, "/api/settings/autopid", "PUT", flags)
        ip = submit_and_wait(ip)
        if ip is None:
            print("FATAL: DUT lost after enabling autopid")
            return 1
    time.sleep(6)

    print("measuring 60 s BASELINE window at the stored periods…",
          flush=True)
    base = measure(ip, 60, label="baseline")
    print(f"baseline: {json.dumps(base)}")
    print("PROGRESS 1/3", flush=True)

    # ---- period_ms = 0 everywhere (live table reload, no reboot) ----
    tables0 = json.loads(json.dumps(tables_before))
    walk_periods(tables0, setter=0)
    api(ip, "/api/autopid/config", "PUT", tables0)
    time.sleep(6)

    print("measuring 60 s MAX-RATE window (all period_ms=0)…", flush=True)
    p0 = measure(ip, 60, label="period0")
    print(f"period0:  {json.dumps(p0)}")
    print("PROGRESS 2/3", flush=True)

    st = api(ip, "/api/status")
    h = st.get("health", {})
    f = api(ip, "/api/faults")

    # ---- verdicts: the device must stay healthy at max rate ----
    check("period0 params still updating", p0["total_hz"] > 0,
          f"{p0['total_hz']:.1f} updates/s observed")
    check("period0 rate >= baseline", p0["total_hz"] >= base["total_hz"],
          f"{base['total_hz']:.1f} -> {p0['total_hz']:.1f}/s")
    check("http alive under max poll", p0["rtt_p50"] < 500,
          f"p50 {p0['rtt_p50']:.0f} ms (baseline {base['rtt_p50']:.0f})")
    check("heap stable over the window",
          p0["int_free_delta"] > -8192 and p0["psram_free_delta"] > -65536,
          f"int {p0['int_free_delta']:+d} B psram "
          f"{p0['psram_free_delta']:+d} B")
    check("zero log errors at max rate", h.get("log_errors", 1) == 0,
          f"log_errors={h.get('log_errors')}")
    check("no faults latched", f.get("faults") == [], f)

    for tag, m in (("BASE", base), ("P0", p0)):
        print(f"METRIC {tag} total updates/s = {m['total_hz']:.2f}")
        print(f"METRIC {tag} http p50 ms = {m['rtt_p50']:.1f}")
        print(f"METRIC {tag} http p95 ms = {m['rtt_p95']:.1f}")

    # ---- restore (tables live; flags only if we flipped enabled) ----
    api(ip, "/api/autopid/config", "PUT", tables_before)

    if enabled_flip:
        api(ip, "/api/settings/autopid", "PUT", flags_before)
        print("restoring; rebooting…")
        ip = submit_and_wait(ip)
    check("restore ok", ip is not None and
          api(ip, "/api/faults").get("faults") == [], "")
    print("PROGRESS 3/3", flush=True)

    if fails:
        print("AUTOPID PERIOD0 FAIL: " + ", ".join(fails))
        return 1
    print("AUTOPID PERIOD0 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
