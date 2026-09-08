#!/usr/bin/env python3
"""ELM327 app responsiveness bench: plays a Car Scanner style request loop
against the WiCAN's obd0 TCP server (port 35000) from the bench Pi while
autopid is configured and polling, and asserts the app gets the chip to
itself with the latency the chip can deliver.

Reproduces the 2026-09-08 field report ("RPM dial choppy, values jump,
feels slow" in Car Scanner): with autopid polling beside the app, 80 of
200 app requests were answered with autopid's own response lines,
`STOPPED` or `NO DATA`, and every response carried a fixed ~20 ms of
RX-task read budget on top of the chip's ~3 ms. The fix: autopid yields
the chip while an external client is active (legacy
DEV_AUTOPID_ELM327_APP_BIT parity, 10 s idle window), the chip RX task
reads on UART events, and the socket sets TCP_NODELAY.

Legs (the probe runs ON the Pi over the hotspot, the API is read from
here; /api/autopid must be enabled with at least one PID polling):
  0  preflight: DUT info, autopid enabled + polling, obd0 up, a clean
     hinted 010C answer over TCP
  1  hinted loop (`010C 1`, 200 requests) while autopid is configured:
     zero foreign/STOPPED/NO DATA answers, p50 <= 12 ms, p95 <= 40 ms,
     >= 40 req/s; /api/autopid reports paused_client during the loop and
     polls_ok does not grow
  2  hint-less loop (`010C`, 200 requests): zero foreign answers (the
     chip's own multi-ECU wait dominates the RTT, so only cleanliness
     and an upper bound are asserted)
  3  resume: paused_client clears and polls_ok grows again within 15 s
     of the app's last command, and the resumed polls SUCCEED (the app
     left the chip with ATS0 etc.; the poller must restore its baseline)

  python elm_app_bench.py [dut host[:port]] [--dut-ip 10.42.1.194]
Expected final line: ELM APP PASS
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import benchlib as bl  # noqa: E402

DUT = "localhost:8081"
DUT_IP = "10.42.1.194"
PROBE = os.path.join(os.path.dirname(__file__), "elm_latency_probe.py")
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--dut-ip":
        DUT_IP = args[i + 1]
        i += 2
    else:
        DUT = args[i]
        i += 1

fails: list[str] = []
metrics: list[tuple[str, object, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def metric(name: str, value, unit: str = "") -> None:
    metrics.append((name, value, unit))
    print(f"METRIC {name}={value}{(' ' + unit) if unit else ''}")


def api(path: str, method: str = "GET", body=None):
    st, text = bl.api(DUT, path, method=method, body=body, classify=False)
    try:
        return st, json.loads(text)
    except Exception:
        return st, text


def autopid_stats() -> dict:
    st, d = api("/api/autopid")
    return d.get("stats", {}) if isinstance(d, dict) else {}


def run_probe(label: str, hint: bool, n: int = 200) -> tuple[dict, list]:
    """Run the probe on the Pi in the background, sample /api/autopid
    while it runs, then return (summary, samples-during-run)."""
    raw = f"/tmp/elm_app_{label}.json"
    cmd = (f"python3 /tmp/elm_latency_probe.py {DUT_IP} --n {n} "
           f"{'--hint ' if hint else ''}--label {label} --raw {raw}")
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={bl.SSH_CONNECT}",
         "rpi001", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    samples = []
    time.sleep(2.0)  # the probe's ELM init (ATZ alone takes ~1.3 s)
    while proc.poll() is None:
        samples.append(autopid_stats())
        time.sleep(1.0)
    out = proc.communicate()[0]
    summary = {}
    for line in out.splitlines():
        if line.startswith("SUMMARY "):
            summary = json.loads(line[len("SUMMARY "):])
        if line.startswith("init ") or line.startswith("HIST "):
            print("   " + line)
    rc, text = bl.pi(f"python3 - <<'EOF'\n"
                     f"import json\n"
                     f"d=json.load(open('{raw}'))\n"
                     f"bad=[x['text'] for x in d['samples'] if x['rtt'] is None"
                     f" or 'STOPPED' in x['text'] or 'NO DATA' in x['text']"
                     f" or ('410C' not in x['text'].replace(' ',''))]\n"
                     f"print(json.dumps({{'bad': len(bad), 'first': bad[:3]}}))\n"
                     f"EOF", timeout=60)
    try:
        summary["bad"] = json.loads(text.strip().splitlines()[-1])
    except Exception:
        summary["bad"] = {"bad": -1, "first": [text[-200:]]}
    return summary, samples


def main() -> int:
    print(f"ELM app bench: API {DUT}, TCP {DUT_IP}:35000, probe via rpi001")

    # ---- leg 0: preflight -------------------------------------------------
    st, info = api("/api/info")
    check("DUT answers /api/info", st == 200 and isinstance(info, dict),
          str(info)[:80])
    print(f"   fw {info.get('fw_version') if isinstance(info, dict) else '?'}")

    st, ap = api("/api/settings/autopid")
    check("autopid enabled (the contention side of the report)",
          st == 200 and ap.get("enabled") is True)
    stats0 = autopid_stats()
    check("autopid has PIDs and is polling",
          stats0.get("pids", 0) > 0 and stats0.get("running") is True,
          f"pids={stats0.get('pids')} running={stats0.get('running')}")
    check("paused_client is reported by /api/autopid",
          "paused_client" in stats0)

    st, socks = api("/api/sockets")
    obd0 = next((s for s in socks.get("servers", []) if s["name"] == "obd0"),
                {}) if isinstance(socks, dict) else {}
    check("obd0 tcp:35000 up", obd0.get("up") is True and obd0.get("enabled"))

    rc, out = bl.sh(f'scp -q "{PROBE}" rpi001:/tmp/elm_latency_probe.py')
    check("probe copied to the Pi", rc == 0, out[-120:])

    # ---- leg 1: hinted loop beside a configured autopid -------------------
    print("-- leg 1: hinted 010C loop (Car Scanner fast mode) with autopid "
          "configured")
    polls_before = autopid_stats().get("polls_ok", 0)
    s1, during1 = run_probe("hint", hint=True)
    print("   " + json.dumps(s1))
    check("leg1: every request answered", s1.get("ok") == s1.get("n") == 200
          and s1.get("timeouts") == 0)
    check("leg1: no foreign / STOPPED / NO DATA answers (autopid yielded)",
          s1["bad"]["bad"] == 0, f"bad={s1['bad']['bad']} {s1['bad']['first']}")
    check("leg1: p50 <= 12 ms (chip floor without the 20 ms read budget)",
          s1.get("p50") is not None and s1["p50"] <= 12.0,
          f"p50={s1.get('p50')} min={s1.get('min')}")
    check("leg1: p95 <= 40 ms", s1.get("p95") is not None and s1["p95"] <= 40.0,
          f"p95={s1.get('p95')} max={s1.get('max')}")
    check("leg1: >= 40 requests/s", s1.get("rate_hz", 0) >= 40.0,
          f"{s1.get('rate_hz')} req/s")
    for k in ("rate_hz", "min", "p50", "p95", "max", "stdev"):
        metric(f"hinted_{k}", s1.get(k), "ms" if k != "rate_hz" else "req/s")
    paused_seen = [s.get("paused_client") for s in during1]
    check("leg1: /api/autopid reported paused_client while the app ran",
          any(p is True for p in paused_seen), str(paused_seen))
    # autopid may legitimately poll until the app's FIRST command (the
    # probe's ssh + connect + ATZ take ~2 s): assert the counter is frozen
    # across every sample taken while the pause was reported
    paused_polls = [s.get("polls_ok") for s in during1
                    if s.get("paused_client") is True]
    check("leg1: autopid polls_ok frozen while paused_client",
          len(paused_polls) >= 1 and max(paused_polls) - min(paused_polls)
          <= 1, f"{polls_before} -> {paused_polls}")

    # ---- leg 2: hint-less loop ---------------------------------------------
    print("-- leg 2: hint-less 010C loop")
    s2, _ = run_probe("nohint", hint=False)
    print("   " + json.dumps(s2))
    check("leg2: every request answered", s2.get("ok") == 200
          and s2.get("timeouts") == 0)
    check("leg2: no foreign / STOPPED / NO DATA answers",
          s2["bad"]["bad"] == 0, f"bad={s2['bad']['bad']} {s2['bad']['first']}")
    check("leg2: p95 <= 120 ms (chip multi-ECU wait, no added stalls)",
          s2.get("p95") is not None and s2["p95"] <= 120.0,
          f"p50={s2.get('p50')} p95={s2.get('p95')} max={s2.get('max')}")
    for k in ("rate_hz", "p50", "p95", "max"):
        metric(f"nohint_{k}", s2.get(k), "ms" if k != "rate_hz" else "req/s")

    # ---- leg 3: resume ----------------------------------------------------
    print("-- leg 3: autopid resumes after the app goes quiet")
    t0 = time.time()
    s0 = autopid_stats()
    base, base_failed = s0.get("polls_ok", 0), s0.get("polls_failed", 0)
    resumed_at = None
    s = s0
    while time.time() - t0 < 20:
        s = autopid_stats()
        if s.get("paused_client") is False and s.get("polls_ok", 0) > base:
            resumed_at = round(time.time() - t0, 1)
            break
        time.sleep(1.0)
    check("leg3: paused_client cleared and polling resumed within 15 s",
          resumed_at is not None and resumed_at <= 15.0,
          f"resumed after {resumed_at} s; polls_ok {base} -> "
          f"{s.get('polls_ok')}, polls_failed {base_failed} -> "
          f"{s.get('polls_failed')}")
    # the app leaves the chip in ITS state (ATS0/ATH1/...): the poller must
    # restore its baseline, so a resumed autopid polls successfully
    time.sleep(3.0)
    s = autopid_stats()
    check("leg3: resumed polls succeed (chip baseline restored after the app)",
          s.get("polls_ok", 0) - base >= 5 and
          s.get("polls_failed", 0) - base_failed <= 2,
          f"polls_ok +{s.get('polls_ok', 0) - base}, polls_failed "
          f"+{s.get('polls_failed', 0) - base_failed}")
    metric("resume_s", resumed_at, "s")

    print()
    for name, value, unit in metrics:
        print(f"  {name:>14} = {value} {unit}")
    if fails:
        print(f"\nELM APP FAIL ({len(fails)}): " + "; ".join(fails))
        return 1
    print("\nELM APP PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
