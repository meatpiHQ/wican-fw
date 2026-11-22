# Bench rig: control plane, recovery ladder, timeout policy (2026-07-26)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

The bench-Pi standardization plan (`docs/bench_pi_standardization_plan.md`)
is EXECUTED. What it changed:

- **Ethernet control plane.** ssh `rpi001` = **192.168.90.2** over the
  direct PC↔Pi cable (static both ends, PC = 192.168.90.1, NO gateway on
  either side: the link must never carry a default route). A dead radio
  can no longer take the bench offline: `rfkill block all` leaves ssh,
  sync, and `sudo reboot` fully working (verified). `rpi001-wifi`
  (192.168.0.83) is the explicit fallback alias.
- **Radio role names** (udev-pinned by MAC on the Pi): `wint0` = internal
  brcmfmac (DUT hotspot `wican-bench-w0`, pinned **10.42.0.1/24** → DUT =
  10.42.0.62 again, the lease flip-flop is dead), `wtest0` = USB stick #1
  (`wican-bench` twin, pinned 10.42.1.1/24), `wtest1` = USB stick #2
  (home-LAN uplink, NON-load-bearing). Old `dnsmasq-wlan{0,1}.leases`
  paths keep working via boot-created symlinks.
- **Recovery ladder** (all reachable over ethernet, all idempotent):
  `bench-health` (seconds; `RIG OK` / `RIG FAULT <comp>: <reason>`;
  truth-tests each active hotspot by **BSSID** from a second radio:
  nmcli "activated" lies), `bench-radio-reset <role> [--usb]` (profile
  bounce → usbreset re-enumeration), `bench-recover` (probe → targeted
  level 0 → usbreset → level-1 Pi reboot with a marker + 10-min
  reboot-loop guard), `bench-startup.service` (asserts canonical state at
  boot, logs `BENCH READY`), `wican-bench-watchdog` (beacon watchdog v2,
  primary = the internal radio). Sources live in `tools/testbench/pi/`;
  install with `deploy_bench_pi.sh`.
- **One bench library, one timeout policy: `tools/testbench/lib/benchlib.py`.**
  New scripts MUST use it; existing ones migrate opportunistically
  (system_bench is migrated). The constants (single source of truth):

  | Constant | Value | Rationale |
  |---|---|---|
  | `SSH_CONNECT` | 10 s + 1 retry | Windows OpenSSH cold-handshake flake |
  | `HTTP_TRY` | 8 s | one API call on a healthy link |
  | `HTTP_RETRY_BUDGET` | 30 s (3 s apart) | rides a route flap; longer = rig fault |
  | `DUT_REBOOT_WINDOW` | 180 s | healthy ≈ 20-40 s; beyond 180 s = rig fault |
  | `OTA_UPLOAD` | 300 s | 3.4 MB at worst-case RF |
  | `BLE_SCAN_TRIES/WINDOW` | 3 × 8 s | existing ble_bench practice |

  **The classification rule:** when a step exhausts its budget, benchlib
  runs `bench-health` BEFORE failing. `RIG FAULT` → `bench-recover`, retry
  once; still faulty → the run fails as **`RIG FAULT <detail>`** (e.g.
  `SYSTEM BENCH RIG FAULT`, exit 2): never blamed on firmware. `RIG OK` →
  the original DUT-side failure surfaces immediately. HTTP status errors
  (4xx/5xx) always surface immediately: they are answers, not transport
  faults.

## The bench lock (2026-11-23)

One bench, one run at a time. The unit, the simulator and the Pi are one bench, and two runs against it spoil each other without either of them failing in a way that names the cause: each restarts the unit under the other, each saves "as found" what the other has staged, and each restore puts the other's test state back. It happened on 2026-11-23 between 08:35 and 08:40: a `profilee2e` run and a Home Assistant backfill bench, started by two sessions five minutes apart. The profile stage saved the backfill bench's one-row table as the unit's own and put it back; the backfill bench restored the profile stage's Leaf as the unit's vehicle; the unit was left with a webhook registered to a Home Assistant that had been stopped. The session that started second had looked at the Pi for a running bench, which does not show a stage that runs from the PC.

**What it is.** A directory on the Pi, `~/.wican_bench_lock`, made with `mkdir` (atomic: whoever makes it holds the bench), with the holder in `owner.json` (its name, what it runs, the PC, the process) and the Pi's clock at the take in `since`. The tool is `tools/testbench/lib/bench_lock.py` (`take`, `release`, `status`, `selftest`).

**`.\test.ps1`** takes it for every kind that touches the bench (not for `list`, `host` and `check`), before the preflight, and gives it back in its `finally`, a failed or interrupted run included. Its lines read `bench lock: TAKEN by test.ps1 <kind> (pid N on <PC>)` and `bench lock: RELEASED ...`. A run that finds the bench held ends in a second: `bench lock: HELD by <who>: <what>, since HH:MM (N min), ...`, `<kind>: NOT RUN`, `RESULT: FAIL`. When the Pi does not answer, no lock can be told: the run says so and goes on.

**A lock that is left behind** (the run was killed) does not block the bench: a lock whose holder's process on this PC is gone is taken over by the next run (`STALE (its process ... is gone), taken over from ...`), whatever its age; a live process keeps its lock however long the stage runs. A lock without a process to look at (taken by hand, or from another PC) is stale past its `--ttl-min` (120 by hand, 720 for a run). `-BreakBenchLock` (`--break`) takes the bench over whatever the lock is: only when that run is known to be gone. A take-over removes the very lock it read (its `since`), in one step on the Pi, so that it cannot remove a lock another run made in the meantime.

**Work by hand** is not behind the runner, so it takes the lock itself, from PowerShell (the Bash tool's ssh does not know the Pi's host key):

```
python tools\testbench\lib\bench_lock.py take --owner <who> --what "<task>"
$env:WICAN_BENCH_OWNER = '<who>'     # .\test.ps1 runs under that lock: it neither takes nor gives it back
python tools\testbench\lib\bench_lock.py release --owner <who>
python tools\testbench\lib\bench_lock.py status
```

A bench script started by hand does not ask for the lock: the lock keeps the runner's stages apart and gives work by hand a way to say "the bench is mine"; it does not stop somebody who does not ask. Before work by hand, `status` first.

**Record, 2026-11-23 09:20 to 09:28.** `python tools\testbench\lib\bench_lock.py selftest`: `BENCH LOCK SELFTEST PASS`, 12 checks, on a lock directory of its own on the Pi (free reads FREE; the first owner takes it; a second is refused while the first one's process lives; the holder asking again is told it is its own; another owner cannot release it; the holder can; a lock whose process is gone is taken over; a lock taken by hand is held inside its time, taken over with `--break`, and stale past its time). The runner: with the bench held by another name `.\test.ps1 target` ended in 1 s with the holder's line, `NOT RUN` and exit 1; with the bench free a run that failed on its own usage error left the bench FREE behind it; under a lock taken by hand and named in `WICAN_BENCH_OWNER` the run went on and the lock was still its owner's afterwards; `.\test.ps1 profilee2e` printed `TAKEN` before its preflight and `RELEASED ... (held 2 min)` behind its report.
