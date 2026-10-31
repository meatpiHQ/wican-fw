# A stored script runs by name: the file read on the flash worker

Command: `python tools/testbench/obd/script_file_bench.py [--dut localhost:18081] [--name fjtest] [--logdir DIR]`

Needs: main fw on DUT through rpi001 (the tunnel); the script engine enabled (`script_engine.enabled`)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** `POST /api/scripts/run {"name": ...}` loads
`/data/scripts/<name>.be` through stdio on the flash filesystem, a flash read,
from the HTTP server's PSRAM stack. Since 2026-11-01 that read runs through
`flash_jobs_run` (the shared internal-stack worker); it was a one-shot 4 KB
internal task of the script engine's own (`se_load`), one of the writers
retired by `internal/TASK_httpd_psram_followups.md`. The Release Test's r11
runs a script by `src`, never by name, so this bench is the loader's gate: a
missed flash path aborts the device with a backtrace within a second (IDF
asserts the stack on every cache-off window), which the reset check would
catch. The event dispatcher's script action takes the same loader
(`script_engine_events.c`). Legs: s1 the script uploaded with
`/api/fs/upload`; s2 run by name answers ok with the script's own output; s3
an unknown name answers 404 (the not-found path, no abort); s4 the file
deleted, no unexpected reset, no E line of `script_engine` / `flash_jobs` /
`filesystem`, the worker's `refusals` unchanged at zero (the
`flash_jobs_marshalled_delta` metric says how many jobs the legs cost).

**Records.** (the first run is appended below)

- 2026-11-01 00:06, the first run on the follow-ups image (`v6.0.0p-beta.6-6-ga73e9ef-dirty`),
  the bench DUT with the script engine off as found (staged on, two planned restarts):
  **`SCRIPT FILE PASS`**: s1 uploaded, s2 `{"ok": true, "output": "script file ok
  2026-11-01"}`, s3 404 `no such script`, s4 deleted, refusals 0, 6 marshalled jobs for the
  legs, the engine back off, no own E line, `httpd` headroom 8932 B, internal floor
  49,572 B. The run before it had passed every functional leg and failed its own reset
  count (the baseline status read after the staging restart: fixed).
