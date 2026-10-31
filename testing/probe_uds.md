# UDS Tool page probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_uds.mjs && node smoke.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**UDS Tool page probe** (prints `UDS PROBE PASS`, 18 checks): path badge + provider chip from the mocked `GET /api/uds`, the Exclusive bus switch and its round-trip (`AutoPID paused` chip after a request), P2* label, 29-bit, the terminal (sent › and reply ‹ lines with the inline decode: VIN as text, NRC name; raw JSON toggle; Clear; persistence in localStorage; a sent line clicks back into the form), the settings card with `exclusive`. WITHOUT `MEATPI_COMPONENTS_PATH` the preview takes the dev layout's live tree, then a clone beside the checkout (since 2026-10-29; before that the stale mirror under `components/meatpi/`, gone since, and it tested the OLD page); it prints the source path: check it. 2026-09-16: PASS, smoke 23 routes 0 errors
