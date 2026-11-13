# MicroLink task ownership

Command: `.\test.ps1 target microlink -Port COMx`.
Expected: `1 Tests 0 Failures 0 Ignored`, then `TEST DONE`, zero E lines.
The run itself takes about 61 seconds. Index: [../TESTING.md](../TESTING.md).

This is a real-FreeRTOS lifecycle test, not a parser test. A deliberately
blocked task is placed in a client's DERP-task slot. The first stop must time
out after 30 seconds without clearing the handle. Start must refuse that
still-owned instance. Destroy invokes another stop and must time out again
without freeing the instance. Once the held task exits, destroy must succeed.

The test inherits the main firmware's configuration and partition table.
Its only console override is 115200 baud for the target runner.
Existing NVS is not erased; no network or vehicle traffic is sent.
The client is created with a dummy auth key that is never sent (init refuses
a config without one). A unit that has run Tailscale before only has its
device keys read (`Keys loaded from NVS`); a unit that never did gets them
generated and stored, as the firmware's first Tailscale start would.
**The stage leaves a test app on the DUT. Restore with
`.\build.ps1 -Flash COMx` before running composed-firmware checks.**

The network reproduction and records are in [tspublic.md](tspublic.md).
The regression specifically guards the case that a retry-loop cancellation
alone cannot prove: a genuinely live task still present after the stop deadline.

## Records

- 2026-11-13 17:24 to 17:26, three runs, FAIL before any assertion ran: the
  first two were build errors of the new app (`console` missing from its
  REQUIRES: the inherited configuration enables the mDNS console commands),
  the third ran and stopped at `microlink: Invalid config: auth_key required`
  (the fixture gave no auth key, one E line, `1 Tests 1 Failures`).
  `2026-11-13_1726_target_microlink.md`.
- **2026-11-13 17:43: PASS**, 82.3 s with build and flash. `Keys loaded from
  NVS`, then `Stopping...`, `a task is still running 30000 ms after the stop
  request` and `start refused: tasks are still owned by this instance` at
  30.5 s; the second `Stopping...` through destroy, the same W line and
  `destroy deferred: a task still owns the instance` at 60.5 s; after the held
  task was released `Stopped` and `Destroyed` within 25 ms.
  `1 Tests 0 Failures 0 Ignored`, `TEST DONE`, errors=0, warnings=4 (the four
  expected W lines above). `2026-11-13_1743_target_microlink.md`.
- **2026-11-13 19:37: PASS** again, 83.1 s, on the tree as committed (the
  relay closed by its own task, the WireGuard device freed, both found after
  the first pass): the same lines at the same 30.5 and 60.5 s, errors=0,
  warnings=4. `2026-11-13_1937_target_microlink.md`. The main firmware went
  back on the unit behind it.
- **2026-11-13 20:20: PASS**, 99.2 s, the tree with the client's bounded
  dial (`ml_dial.c` in the app's build) and the test file's `@file` comment:
  the same lines, errors=0, warnings=4. `2026-11-13_2020_target_microlink.md`.

## Traps

- The runner's capture window is 90 s and the app needs 61 s of it: a third
  deadline in the test would need a longer window in `test.ps1`
  (`Invoke-TargetApp`).
- A failing Unity assertion prints `TEST FAILED` and never `TEST DONE`: the
  runner then waits out its window and reports FAIL.
