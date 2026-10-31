# Power Saving page probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_power.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Power Saving page probe** (prints `POWER PROBE PASS`, 11 checks, 2026-10-01): the sleep_manager v3/v4 fields as the schema-driven form renders them: `Wake-up voltage (V)` as a volt slider (13.2 V, 12.1 to 15) next to the sleep voltage, `Wake up after (s)` in SECONDS (0.5 s, 0.1 to 5, the `SEC_MS_KEYS` rule) whose change stages MILLISECONDS (2 s -> `wake_delay_ms: 2000` in `store.pending`, nothing PUT, header `Submit Changes (1)`), help text naming spikes vs a faster wake, `Sleep after (min)`, the status chip with ONE decimal. 2026-10-01: PASS
