# A config.json with a parameter name twice

Command: `python tools/testbench/system/autopid_names_bench.py [--wican auto] [--dut 10.42.1.194]`

Needs: PC + rpi001 + DUT (console)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**A config.json with a parameter name twice** (prints `AUTOPID NAMES PASS`, 14 checks, ~45 s, two planned restarts; 2026-10-06, Ali's phone: "duplicate parameter name 'OxySensor1_Volt'" at Finish; the old SAE table gave one name to two PIDs and the detection stored the rows unvalidated, so the device's own loader refused the file at the next boot: `E autopid: config file invalid: ..., empty tables` + a `boot_errors` fault, reproduced on the DUT before the fix). The bench reads `/api/autopid/std_table` (no parameter or row name twice, the wide-range oxygen rows under their own names), uploads the file the old firmware wrote (0114 + 0124 + 010C with the firmware's own expressions) through `/api/fs/upload`, restarts: one W line `config file: 1 repeated parameter name made unique (_2, _3, ...) and the file rewritten`, no autopid E line, `config: 1 groups, 3 pids, 0 filters, 5 params`, no fault, the GET returns `OxySensor1_Volt_2`, the PUT of what was read is 200, a second restart has nothing to repair (change-guarded rewrite), no unexpected reset, 0 E lines beyond esp-mqtt's reconnect lines. Leaves the DUT with empty tables. 2026-10-06: PASS
