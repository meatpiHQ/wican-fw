# Rules builder probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_rules.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Rules builder probe** (prints `RULES PROBE PASS`, 23 checks): sentence rows with the technical line, `·auto` tag and runtime badges from the mocked `/api/events/rules`; Templates menu; the charging template read back into Trigger / Only if / Then; adding a LIVE-value condition (autopid parameter expanded from the `autopid.` prefix) with its badge and preview text; Edit as JSON showing `when[].value` + `undo` and the round trip back; Add staging the rule (header dirty, sentence row with the undo tail); the WiFi template (network picker, rate, undo) and a rate edit; edit locks the name; duplicate suffixes `_2`; a bad name is refused with the modal open. 2026-09-17: PASS
