# Stack + memory audit, both halves

Command: `.\test.ps1 stackaudit`

Needs: static: local build; runtime: main fw on DUT + rpi001

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Stack + memory audit, both halves** (2026-07-22, born from the silent PSRAM-stack overflow): (1) *static*, `tools/stack_audit.py` reads the compiler's per-function frame sizes (`-fstack-usage` is permanently on; `.su` files land next to every `.obj`) and cross-references them against every `xTaskCreate*` stack size (PSRAM vs internal detected), flagging frames > 40% of a same-component task stack for human triage; (2) *runtime*: `stack_audit_test.py` on the Pi exercises the deep paths (DTC scan job, test-a-PID), then asserts EVERY live task's `stack_hw` >= 512 B (warn < 1024), the ephemeral job tasks' exit-log watermarks (`dtc job stack_hw=` / `std scan stack_hw=` via `/api/logs/ring`), and heap floors on BOTH placements (internal min_free >= 20 KB + largest_block >= 16 KB, PSRAM min_free >= 1 MB). The runtime half also runs inside `live`

**2026-10-31 (the HTTP server task on a PSRAM stack, `internal/TASK_httpd_psram.md`): PASS** on the final image, with the two new checks `httpd stack in PSRAM (stack_ext True stack_hw 8948)` and `fs_writer stack in PSRAM (stack_ext True stack_hw 4420)` (the task monitor rows carry `stack_ext` since that day and the tables hold 64 rows: 52 tasks on the bench DUT, the 48-row cap had hidden `fs_writer`); the static half lists the `flash_jobs` worker with a `?` placement (a plain `xTaskCreate`: internal heap); min_free 41,964 B, largest block 31,744, the dtc job headroom 14,192 B, faults clean. Report `2026-10-31_2131_stackaudit.md`.

**2026-10-31 23:58 (the follow-ups: the Tailscale stacks on PSRAM, the worker at 4 KB, the
vehicle writer's stack on PSRAM, `internal/TASK_httpd_psram_followups.md`): PASS** (with the
known IDLE/ipc/usb warnings), `httpd stack in PSRAM (8932)`, `fs_writer stack in PSRAM
(4420)`, the `vpn_state` / `ml_wg_mgr` checks conditional (absent with the VPN off; the
tspublic stage asserts them with the tunnel up), min_free 53,044 B (41,964 the night
before: the vehicle writer's 6 KB `.bss` and the worker's 2 KB), PSRAM min_free 3.67 MB;
the static half lists `flash_jobs` as `int` now (a dynamic task reads as internal whatever
its size expression) and `vpn_state` as PSRAM (a `StackType_t *` buffer taken with
`MALLOC_CAP_SPIRAM`); `em_worker` (event_manager) still reads `?`. Report
`2026-10-31_2358_stackaudit.md`.

**2026-11-01 02:01 (the NimBLE host task on a 6 KB PSRAM stack,
`internal/TASK_ble_host_psram.md`): PASS** (the known IDLE/ipc warnings), `httpd stack in
PSRAM (8940)`, `fs_writer stack in PSRAM (4420)`, the new `nimble_host stack in PSRAM` check
conditional like the VPN tasks' (absent on this run: Bluetooth was off since the boot before
it; `blesec` asserts it with Bluetooth on, 3076 B of headroom after a pairing), 49 tasks,
min_free 51,604 B, largest block 31,744, PSRAM min_free 3.61 MB, the dtc job headroom
14,192 B, faults clean. The static half read `nimble_host` as `? PSRAM` on that run (its size
is `CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE + 2048`, a Kconfig value the resolver could not
see); `stack_audit.py` reads `build/config/sdkconfig.h` since the same night and resolves
macro bodies through each other, so it prints `6144 PSRAM nimble_host`, and a row of a 2-D
stack array (`s_worker_stack[i]`) finds its declaration: `em_worker` reads `4096 PSRAM`
instead of `?`. Report `2026-11-01_0201_stackaudit.md`.

**2026-11-14 04:11 (the CAN filters list and the filter window's two legacy rules, [canfilters.md](canfilters.md)): PASS** (the known IDLE/ipc warnings), `httpd stack in PSRAM (7224)`, `fs_writer stack in PSRAM (4420)`, 49 tasks, internal min_free 48,428 B, largest block 31,744, PSRAM min_free 3.38 MB, the dtc job headroom 14,192 B, faults clean. The static half: the poller `autopid` 12,288 B PSRAM, its deepest new frame `flt_window` 816 B. Read by hand in the same boot, with a table of filters polled and two filter tests run (an 11-bit ID and a 29-bit one, which takes the protocol switch): `autopid` 9,288 B of headroom before and 9,208 B after, `httpd` 7,224 B before and after. Report `2026-11-14_0411_stackaudit.md`.
