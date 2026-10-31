# Stack + memory audit, both halves

Command: `.\test.ps1 stackaudit`

Needs: static: local build; runtime: main fw on DUT + rpi001

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Stack + memory audit, both halves** (2026-07-22, born from the silent PSRAM-stack overflow): (1) *static*, `tools/stack_audit.py` reads the compiler's per-function frame sizes (`-fstack-usage` is permanently on; `.su` files land next to every `.obj`) and cross-references them against every `xTaskCreate*` stack size (PSRAM vs internal detected), flagging frames > 40% of a same-component task stack for human triage; (2) *runtime*: `stack_audit_test.py` on the Pi exercises the deep paths (DTC scan job, test-a-PID), then asserts EVERY live task's `stack_hw` >= 512 B (warn < 1024), the ephemeral job tasks' exit-log watermarks (`dtc job stack_hw=` / `std scan stack_hw=` via `/api/logs/ring`), and heap floors on BOTH placements (internal min_free >= 20 KB + largest_block >= 16 KB, PSRAM min_free >= 1 MB). The runtime half also runs inside `live`

**2026-10-31 (the HTTP server task on a PSRAM stack, `internal/TASK_httpd_psram.md`): PASS** on the final image, with the two new checks `httpd stack in PSRAM (stack_ext True stack_hw 8948)` and `fs_writer stack in PSRAM (stack_ext True stack_hw 4420)` (the task monitor rows carry `stack_ext` since that day and the tables hold 64 rows: 52 tasks on the bench DUT, the 48-row cap had hidden `fs_writer`); the static half lists the `flash_jobs` worker with a `?` placement (a plain `xTaskCreate`: internal heap); min_free 41,964 B, largest block 31,744, the dtc job headroom 14,192 B, faults clean. Report `2026-10-31_2131_stackaudit.md`.
