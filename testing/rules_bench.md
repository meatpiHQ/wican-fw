# Rules-engine bench

Command: `python tools/testbench/rules_bench.py --base http://localhost:8081`

Needs: PC + DUT over the tunnel

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Rules-engine bench** (prints `RULES BENCH PASS`, 23 checks, 4 restarts; `--skip-wifi` / `--skip-stack` drop phases B / D): the registry surfaces (`wifi.sta` source, `wifi.*` live values, `undoable` flags, `GET /api/events/rules`, `events_src`/`events_act` headroom in `health.caps`); a `wifi.sta` while-rule fires on the STA connect at boot and overrides the group rate (skip with `--skip-wifi`); a timer rule with a live `${time.epoch}` condition + undo is undone by the dispatcher's 1 s re-check once the DEVICE clock passes the deadline (the DUT's clock differs from the PC's by minutes: the script derives now from restart_tracker boot_time + uptime); phase D stacks the WiFi and the epoch while-rules on ONE group plus a 3 s timer rule with a 10 s cooldown (both arm; the epoch undo restores the CONFIGURED rate, the documented stacking semantic; the cooldown holds the beat to about one run per window with `stats.suppressed` growing); the device's rules are restored at the end. Host side: `.\test.ps1 host event_manager` runs the 28-test suite incl. 12 replayed rule-combination scenarios. 2026-09-17: PASS
