# Connections card probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_connections.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Connections card probe** (prints `CONNECTIONS PROBE PASS`, 12 checks): seeds the FACTORY bridges (OBD chip on WebSocket + TCP 35000 + USB at once) and expects three rows with no warning, before 2026-10-01 the card claimed both ends of every connection and refused the defaults with "obd is already used by br_obd: one connection per interface", so Submit never got the change, while the firmware's obd (and can) jacks are multi-consumer and allow it (`bridge_endpoints.c`, `bm_validate_bridges`). Still policed: a second connection on the same TCP server (`obd0 is already used`); a new port is accepted (OBD chip shared four ways); six connections accepted (`BRIDGE_MANAGER_MAX_BRIDGES`, the card said 4), the seventh refused by the Add button's toast; no `max 4` left. 2026-10-01: PASS
