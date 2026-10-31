# Autopid DTC/DBC PCAN benches (PC-run, 2026-07-22)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`tools/testbench/obd/dtc_bench_test.py [dut_ip] [pcan]` (→ `DTC BENCH PASS`)
and `dbc_bench_test.py` (→ `DBC BENCH PASS`) drive the DUT over HTTP
with PCAN as the "car" side; run them from the PC with the IDF venv
python (has python-can). When the DUT USB is in CH342 mode, flip it to
NCM first (usb_host_manager `{enabled:true, role:"device",
device_class:"ncm"}` + submit → PC talks to `192.168.82.1`; flipping
back needs the settings revert + a REAL power cycle: the bench PSU's
output off/on works remotely).

- **DTC**: legs 0-7 (gates, scan, conditional clear, new-code events)
  + **leg 6c multi-ECU**: `pcan_obd_ecu.py` answers as TWO responders
  (`--resp2 0x7EA`, own DTC lists; keep each stored list <= 2 codes so
  responses stay single-frame) with `dtc_rxheader` cleared; asserts the
  merged report (dedup pinned by a shared code, `ecus >= 2`, MIL OR +
  count sum). The hardware ECU-sim box (7E8) is a third live responder:
  list assertions are supersets on purpose.
- **DBC**: fixture has a real mux switch + an extended-mux message;
  asserts m1 signals compile WITH `mux_expr`/`mux_val`, extended stays
  listed-with-reason, and the LIVE leg broadcasts BOTH mux pages
  (`pcan_dbc_broadcast.py --alt`) with a decoy value on the wrong page
  that must NEVER leak into the parameter. Assertions are DELTA-based
  against the pre-bench config, so debris from an earlier aborted run
  can't fail it.
- `dbc_real_test.py` (also a `live` stage): since simple-mux support,
  the four real DBC fixtures are **100% supported** (tesla 240/240 incl.
  its 28 muxed signals, ford 2150/2150, bmw, j1939) and stay field-exact
  vs the host reference parser.
- `wifi_profile_ab_test.py` (→ `WIFI PROFILE AB DONE`): decision-grade
  `wifi_ram_profile` full-vs-lean comparison: internal-RAM floors,
  iperf2 TCP both directions, UDP + loss, HTTP RTT; switches the
  profile via submit-reboot and restores the starting profile.
- `mqtt_can_bench_test.py` (→ `MQTT CAN BENCH PASS`), the CAN⇄MQTT
  bridge: gates-closed-first, RX conservation vs mosquitto, byte-exact
  TX via PCAN, custom topics; needs the Pi broker + PCAN. NOTE it
  PARKS any bridge that owns the single-consumer `can` jack for the
  run and restores everything after.
- `uds_dtc_bench_test.py` (→ `UDS DTC BENCH PASS`): UDS 0x19/0x14
  DTC vs the ECU-sim box (sim REST at 192.168.8.1 from the PC):
  forced-`uds` scan, group clear, `auto`-prefers-OBD leg. The sim's
  19 02 ignores the status mask and its 0x14 ignores groupOfDTC:
  the bench works around both (documented in TESTBENCH.md row).
