# A full-scale reading is never dropped by its own range

Command: `python tools/testbench/obd/autopid_bounds_bench.py [--dut localhost:18081] [--watch 20]` (PC, the DUT through an ssh tunnel, the simulator through sim_net)

Needs: PC + the ECU simulator; the DUT on the bench AP, the PSU above the sleep voltage; 2026-10-28: the bench moved to the 2026-10-24 frame of reference (the table hands out B3*100/255 now, the custom rows read B3); on the stale numbering the custom rows read 18.43 (byte 2F, the PID echo), the firmware was right; PASS again that evening on the moved numbering (the raw check takes the printed header)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**A full-scale reading is never dropped by its own range** (prints `AUTOPID BOUNDS PASS`, 19 checks, ~1.5 min, up to two planned restarts; issue #1004, 2026-10-20: a VW T6.1's full tank answered `41 2F FF`, the standard FuelTankLevel row decoded it as `B2*0.3921568692` = 100.000001646, a hair above its max of 100, and the poller dropped it: stale dashboard, MQTT kept the old value, the Test button showed 100.000001646). The ECU simulator's fuel level is set to 100 % (raw FF) and four rows are polled every second: the alfa-04 standard row as stored (must read exactly 100: clamped), a control row (CalcEngineLoad 25.1 %), a custom row with the same float noise (100 too: the tolerance) and a custom row decoding 102 against a max of 100 (stays dropped: a custom row's bounds are a filter); then `GET /api/autopid/std_table` must hand out the fuel row as `B2*100/255` with max 100 and the test route must decode the full tank as exactly 100 with it. Restores the simulator's level, the tables and the settings.

**Reproduced before the fix (2026-10-20):** FuelTankLevel and the noisy custom row null while the control row read, `polls_failed` +15 in 20 s, the table's expression `B2*0.3921568692`, the test route 100.000001646; PASS after it
