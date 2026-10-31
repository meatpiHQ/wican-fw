# A car WiCAN was not set up for: the bar, and the fold-in that brings the chosen rows back

Command: `python tools/testbench/obd/autopid_newcar_bench.py [--dut localhost:18081]` (PC, the DUT through an ssh tunnel, the simulator on its own interface, Chrome through Playwright from `tools/webui_preview`)

Needs: PC + rpi001 + the DUT + the ECU simulator (its default profile active, its car current in the store); 2026-10-28: the close puts /data/autopid/vehicles.json and the VIN car's file back as read at n0 and reloads them (a restart), so the fold-in's face does not outlive the bench; PASS 26 checks that evening with the close proven: the files put back, no face left

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**A car WiCAN was not set up for: the bar, and the fold-in that brings the chosen rows back** (2026-10-26, prints `AUTOPID NEWCAR PASS`, ~4 min, 3 planned restarts): the VIN car with exactly RPM and speed on and polled (Ali's state after the Quick Setup); the simulator then plays the fp-dup fixture (the same ECU with seven PIDs disabled) with 0902 and 22F190 silenced and the DUT restarts (his reboot / OBD replug): a second car `fp:` becomes current, the live table is its rows all off, nothing polled, `GET /api/status` carries `vehicle` with `pending` true and `cars` 2, the Dashboard in Chrome shows the bar and the banner; `POST .../merge {"into": <VIN>}` folds it into the VIN car: `vehicle.pending` false, the two rows back and read, the bar gone. The simulator, the store, the config and the settings go back at the end.
