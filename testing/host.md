# Host unit suites (components/*/host_test, 526 tests across 44 components as of 2026-07-26

Command: `.\test.ps1 host [component]`

Needs: `ssh rpi001` reachable (the WSL form: gcc in WSL, the IDF tree on C:)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

Host **unit** suites (`components/*/host_test`, 526 tests across 44 components as of 2026-07-26; the summary prints a per-suite and TOTAL count) on the bench Pi's IDF linux target, syncs sources automatically (the tree it syncs is printed as `components: <path>`: since 2026-10-29 the dev layout's live tree by default, `MEATPI_COMPONENTS_PATH` to override, and nothing found stops the run; before that an unset variable silently synced the stale `components/meatpi` clone, which is gone). Optional component name runs just that suite.

**Pi offline:** `bash tools/testbench/host_gcc_wsl.sh <vpn_manager|config_backup>` from WSL compiles that PURE suite with the system gcc + Unity under ASan/UBSan (no IDF build; 2026-10-16, issues #985 to #989: `vpn_manager` 11, `config_backup` 24; add a case per IDF-free suite)
