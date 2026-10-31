#!/usr/bin/env python3
"""A stored script runs by name: the file read on the flash worker (2026-11-01).

`POST /api/scripts/run {"name": ...}` loads `/data/scripts/<name>.be` through
stdio on the flash filesystem, a flash read, from the HTTP server's PSRAM
stack: since 2026-11-01 that read runs through `flash_jobs_run` (it was a
one-shot 4 KB internal task of the script engine's own, `se_load`); a missed
path would abort the device with a backtrace within a second (IDF asserts
the stack on every cache-off window). The event dispatcher's script action
takes the same path (`script_engine_events.c`).

Legs:
  s1  the script uploaded to /data/scripts/<name>.be (`/api/fs/upload`)
  s2  run by name answers ok with the script's own output
  s3  an unknown name answers 404 (the loader's not-found path, no abort)
  s4  the file deleted, no unexpected reset, no E line of the engine's tags,
      the worker's refusals still zero

  python tools/testbench/obd/script_file_bench.py [--dut localhost:18081]
      [--name fjtest] [--logdir DIR]

Prints `SCRIPT FILE PASS`.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("script_engine", "flash_jobs", "filesystem")
SRC = 'print("script file ok 2026-11-01")\n'


def upload(base, path, body):
    req = urllib.request.Request(base + "/api/fs/upload?path=" + path,
                                 data=body.encode(), method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--name", default="fjtest")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    run = pcbench.Run("SCRIPT FILE")
    dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("httpd",))
    path = f"/data/scripts/{a.name}.be"
    exit_code = 1
    found_enabled = None
    try:
        dut.as_found()
        # the bench DUT's canonical settings keep the script engine off: stage
        # it on for the run (reboot-to-apply, one planned restart) and put it
        # back at the end, the Release Test's r11 pattern
        found_enabled = dut.settings("script_engine").get("enabled", True)
        st0 = dut.get("/api/status")   # before any restart: the reset check counts them
        if not found_enabled:
            print("script engine off as found: staging it on (a restart)", flush=True)
            dut.restart({"script_engine": {"enabled": True}})
        # the worker's counters start at zero with every boot: read them now
        fj0 = dut.get("/api/status").get("health", {}).get("flash_jobs", {})

        # s1: the script on the flash filesystem
        code, body = upload(dut.base, path, SRC)
        run.check("s1_uploaded", code == 200, f"{code} {body[:80]}")

        # s2: run by name (the loader on the flash worker)
        code, r = dut.api("/api/scripts/run", "POST", {"name": a.name}, timeout=30)
        ok = code == 200 and isinstance(r, dict) and r.get("ok") is True \
            and "script file ok" in json.dumps(r)
        run.check("s2_runs_by_name", ok, f"{code} {json.dumps(r)[:160]}")

        # s3: an unknown name: 404, no abort
        code, r = dut.api("/api/scripts/run", "POST", {"name": "no_such_script_x"},
                          timeout=30)
        run.check("s3_unknown_name_404", code == 404, f"{code} {json.dumps(r)[:120]}")

        # s4: the file gone, the device as found
        code, r = dut.api("/api/fs/file?path=" + path, "DELETE")
        run.check("s4_deleted", code == 200, f"{code}")
        st1 = dut.get("/api/status")
        fj1 = st1.get("health", {}).get("flash_jobs", {})
        run.check("s4_no_worker_refusal",
                  fj1.get("refusals", 0) == fj0.get("refusals", 0) == 0,
                  f"refusals {fj0.get('refusals')} -> {fj1.get('refusals')}, "
                  f"marshalled {fj0.get('marshalled')} -> {fj1.get('marshalled')}")
        run.metric("flash_jobs_marshalled_delta",
                   (fj1.get("marshalled") or 0) - (fj0.get("marshalled") or 0))
        dut.reset_check(run, st0)
        if found_enabled is False:
            dut.restart({"script_engine": {"enabled": False}})
            run.check("s4_script_engine_back_off",
                      dut.settings("script_engine").get("enabled") is False)
            found_enabled = None  # restored
        dut.passing_checks(run)
        exit_code = run.verdict()
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        run.fails.append(f"bench: {e}")
        run.verdict()
        exit_code = 2
    finally:
        if found_enabled is False:
            try:
                dut.restart({"script_engine": {"enabled": False}})
            except pcbench.Bench as e:
                print("restore by hand: script_engine.enabled=false:", e, flush=True)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
