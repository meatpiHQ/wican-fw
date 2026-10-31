# BenchBoard: the interactive test dashboard (2026-07-26)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

**`C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe tools\benchboard\benchboard.py`**
→ `http://127.0.0.1:8777/`, a local web UI over the whole bench:
catalog of tools + tests from the repo-root `benchboard.toml` (grouped,
searchable, per-test parameter forms), live-streamed runs with
PASS/FAIL/METRIC highlighting and cancel, a queue with resource locks
(one DUT = sequential by default), verdict classes incl. a distinct
**RIG FAULT** badge, sqlite history with full logs and captured METRIC
lines, instrument health chips, and a per-run **progress bar** (flow
steps and `PROGRESS k/n` output lines are exact; otherwise a dim
elapsed/budget estimate). Generic tool: design record
`docs/benchboard_plan.md`, usage `tools/benchboard/README.md`;
Python 3.11+ (stdlib only; plain `python` may be the Store 3.10: use
the IDF v6 venv). Its own test suite: `tools/benchboard/tests/`
(`benchboard-selftest` in the UI). Adding a bench? Print a final
`<NAME> PASS` line, exit non-zero on failure, add a `[[test]]` entry.

**Full coverage since 2026-07-27** (`docs/benchboard_full_coverage_plan.md`):
every test type in this doc is now on the dashboard, target test apps
via the `target-cycle` flow (flash one component's app on the `serial`
instrument's port, capture the verdict, then ALWAYS restore the main
firmware and wait for the DUT to return, even when the app fails
mid-run), the web UI jsdom smoke as `webui-smoke` (regenerates
preview.html each run), and the public-export release gates as the
`export-gates` action (git stays manual).

**Narration convention** (2026-07-26, "show what's going on"): a bench
must never go silent for long: (1) announce each phase before starting
it ("tier 2/3 (50% load): sending exactly 5000 frames…"), (2) print a
heartbeat every ~5-10 s inside any window longer than ~10 s (storm
counts, reboot waits, measurement windows), (3) print `PROGRESS k/n`
when milestone k of n completes: BenchBoard's bar tracks it exactly
(flows get this for free from their steps). New prints use
`flush=True`; BenchBoard runs scripts with `-u` but console pipes may
buffer.

**Instruments = plugin folders** (`tools/testbench/instruments/<id>/`,
one folder per piece of bench equipment: `instrument.toml` + probe
script; `_template/` is the starter, `README.md` there is the format
spec). **Porting to another bench edits NOTHING tracked**: copy
`benchboard.local.example.toml` → `benchboard.local.toml` (gitignored)
and set your COM ports / IPs / device id / `enabled=false` for gear you
don't have. `${python}` and `${<instrument>.<key>}` substitute into
every manifest cmd, so each machine-specific value lives in exactly one
place.
