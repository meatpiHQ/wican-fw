# Script UDS bindings

Command: `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/uds_bindings.be [--tx 7E2 --rx 7EA]`

Needs: main fw on DUT + the ECU simulator

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Script UDS bindings** (prints `SCRIPT UDS PASS` + `SCRIPT RUN PASS`, ~3 s): runs a Berry script through `POST /api/scripts/run` exercising uds()/uds_ok/uds_nrc/uds_nrc_str, obd_claim/obd_request/obd_isotp_tx/rx/obd_release against the ECU simulator (7E0/7E8; `--tx/--rx` rewrite the script's `var TX/RX` header). Needs `script_engine.enabled`. Bench 2026-09-16: 13/13 on the native ISO-TP path with autopid running, and on the forced obd_chip path
