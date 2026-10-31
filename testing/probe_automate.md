# Automate page probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_automate.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Automate page probe** (prints `PROBE PASS`, ~58 checks): Settings/Parameters/Destinations/HA tabs, PID lists (collapsed parameters, scan results merge + dedup; since 2026-09-22 the Scan results picker starts with NOTHING ticked, already-configured rows disabled, and Add selected with no ticks adds nothing), and since 2026-09-17 the Vehicle Specific flow: ONE `Vehicle profile` row + `Choose profile` dialog over the mocked `vehicle_profiles.json` (makes left / models right, make click lists its models, search narrows both and auto-selects a single match with highlight, disabled `Use this profile` until a model is selected, ArrowDown/Enter), staging without any config PUT (banner + `Unsaved edits`), Discard restoring the device's state, Apply PUTting the config once then the save-settings modal (Save, restart later -> one settings PUT, header Saved, Restart now notice on the card, fields still filled after leaving and re-entering the page), Test modal transcript, Add PID, custom PIDs surviving the import. 2026-09-17: PASS; 2026-09-22: PASS (65 checks, plain + `--min`)
