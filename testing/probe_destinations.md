# Automate > Data destinations tab probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_destinations.mjs` (and `python make_preview.py --min && node probe_destinations.mjs`)

Needs: PC, node + jsdom

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The destinations tab over the mock's three rows (an MQTT topic, an HTTPS
endpoint with a bearer token and a cert set, an ABRP row active on `Garage`
while the mock's station is on `HomeWiFi`; the probe presets two backup
networks, `Garage` and `Office`). Since the rework of 2026-11-08 (Ali's
review of the screenshots: the status column, the dropdown for the
networks):

- the header (On, Type, Topic or URL, Every, Status), the four types;
- the status chips in their column with the fixed words and the page's dot:
  `Sending · 412`, `Failing · retry in 10 s`, `Waiting for Garage`;
- the row's facts under its topic or URL (`All networks · retained`, the
  networks, the bearer token, the cert set, the first push, the car model);
- the Links row (the network with its name, MQTT connected, no banner);
- Options as a named button on every row, no bare caret; the options panel
  under the row with Active on first and Delivery last (failed 3 in a row,
  backing off 10 s, the last error, the laps skipped for the network);
- the Active on dropdown: closed until pressed, the list (All networks,
  then HomeWiFi main network connected now, Garage backup 1, Office backup
  2), ticking a network adds it to the button and the row's facts and
  stages the change, ticking All clears the names, ticking a network again
  drops All, unticking the last one puts All back, a click elsewhere closes
  the list;
- the ABRP options (token, key, placement, car model, the ABRP fields line,
  the 5 s floor on the period);
- Add destination (a new row expanded, `Not applied yet`, its Test disabled
  until the restart), the type switch to ABRP, Test on a saved row, delete,
  the legacy timer-rule import.

## Records

- 2026-09-19: the first probe, 24 checks.
- 2026-11-08: the reworked table, `PROBE PASS` (47 checks) plain and with
  `--min` (`test-reports/logs/chip_prompt_2026-11-08/probes_all.log`).

## Traps

- The page keeps a 5 s stats poller: the probe calls `process.exit()` itself.
- `panel()` is the first open options panel in the DOM: close a row's
  options before reading another row's.
- A probe that presets `__mockPreset.wifi` changes the mock's wifi_manager
  values for that boot only.
