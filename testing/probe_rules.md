# Rules builder probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_rules.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Rules builder probe** (prints `RULES PROBE PASS`, 23 checks): sentence rows with the technical line, `·auto` tag and runtime badges from the mocked `/api/events/rules`; Templates menu; the charging template read back into Trigger / Only if / Then; adding a LIVE-value condition (autopid parameter expanded from the `autopid.` prefix) with its badge and preview text; Edit as JSON showing `when[].value` + `undo` and the round trip back; Add staging the rule (header dirty, sentence row with the undo tail); the WiFi template (network picker, rate, undo) and a rate edit; edit locks the name; duplicate suffixes `_2`; a bad name is refused with the modal open. 2026-09-17: PASS

**2026-11-21, the action "Send a CAN frame" (can.send, GitHub issue #1072): `RULES PROBE PASS`, 28 checks,
on the plain page and on the minified one (`make_preview.py --min`); `node smoke.mjs` 24 routes, 0 errors.**
Five new checks: the action list offers it, its fields are Bus (the car's OBD protocol first, five
choices), ID and Data with their defaults, the preview reads "send CAN frame 7DF 02 01 0C on The car's OBD
protocol", edits of the bus and the frame reach the preview, and Edit as JSON shows the engine's shape
(`"do": "can.send"`, `with` `bus`, `id`, `data`). The mock's `/api/events/actions` lists `can.send`.
