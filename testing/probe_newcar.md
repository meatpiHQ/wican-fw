# The new-car bar

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_newcar.mjs`

Needs: PC only (jsdom)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The new-car bar** (2026-10-26; prints `NEWCAR PROBE PASS`, 14 checks): Ali set a device up on the simulator, took it to his car, and the Dashboard was empty without a word (the car answered as a new one and became current with its rows stored off). Over the mock: a pending car current beside a set-up one on the Dashboard puts the bar up (the lead, the car named with "(no VIN)", Set up this car to the vehicle step, Not now per car in this browser) and the banner above the tiles; no bar on the Quick Setup, with one car only, or with the set-up car current.
