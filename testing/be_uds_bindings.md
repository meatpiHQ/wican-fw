# Script UDS bindings

Command: `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/uds_bindings.be [--tx 7E2 --rx 7EA]`

Needs: main fw on DUT + the ECU simulator

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Script UDS bindings** (prints `SCRIPT UDS PASS` + `SCRIPT RUN PASS`, ~3 s): runs a Berry script through `POST /api/scripts/run` exercising uds()/uds_ok/uds_nrc/uds_nrc_str, obd_claim/obd_request/obd_isotp_tx/rx/obd_release against the ECU simulator (7E0/7E8; `--tx/--rx` rewrite the script's `var TX/RX` header). Needs `script_engine.enabled`. Bench 2026-09-16: 13/13 on the native ISO-TP path with autopid running, and on the forced obd_chip path

**A long answer, whole (2026-11-26).** A script got the first 128 bytes of an ECU's answer as hex and nothing about the rest, and `nil` for an answer over 512 bytes, from `uds()` / `uds_ext()`, `obd_request()` and `obd_isotp_rx()` alike. All three now hand over the whole answer, up to the largest ISO-TP message of 4095 bytes (`limits.resp_max_bytes` on `GET /api/scripts/reference`). The script reads the simulator's memory for it (`23 14 00 00 00 00 C8`, 201 bytes of answer; `23 24 00 00 00 00 0F FE`, 4095 bytes) and checks the length of the hex, its start and its last byte; an ECU that does not serve that memory (the PCAN reflash ECU behind `--tx 7E2 --rx 7EA`) skips those four checks with a `SKIP:` line.

Records, bench unit 14c19f457fd9, native ISO-TP path, the script engine switched on for the run and off again (two planned restarts):
- Before (the firmware with every other fix of that day, not this one): `SCRIPT UDS FAIL (4)`. `uds()` and `obd_isotp_rx()` returned 383 characters (128 bytes) for the 201-byte answer; `uds()` and `obd_request()` returned `nil` for the 4095-byte one.
- After: `SCRIPT UDS PASS`, 17 of 17: 602 characters for 201 bytes, 12284 characters for 4095 bytes, from each of the three calls that was asked.

Trap: a script that prints a long answer still meets the run's output limit (`limits.out_max`, 4096 characters): the returned string is whole, what `log()` shows of it is not. The checks print the length, not the answer.
