# OBD chip and CAN self-test cards probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_diag.mjs` (and `python make_preview.py --min && node probe_diag.mjs --min`)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**OBD chip and CAN self-test cards probe** (prints `DIAG PROBE PASS`, 30 checks): the cards land on System > Maintenance after the Maintenance card; the OBD chip card shows the state row, the down banner with the bring-up's wire notes when the mock chip is down, the outdated banner naming both versions on a chip left on V2.3.22 (and none on a chip at the packaged version), the rewrite confirmation (Cancel / Continue) and the forced POST, the configuration block from `/api/obd_chip/report` (409 = a banner), the wire tools with their console; the CAN self-test card runs and shows the verdict chip with the result as one copyable block in the console's text, and a refusal as not run with its reason and no block; the Maintenance card leads the page, the backup cards follow the diagnostics. Plain and `--min` (2026-10-28, issue #1023)
