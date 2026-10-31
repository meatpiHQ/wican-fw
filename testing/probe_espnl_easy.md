# The ESPNetLink easy setup over the mock

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_espnl_easy.mjs`

Needs: PC (Chrome)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The ESPNetLink easy setup over the mock** (prints `ESPNL EASY PROBE PASS`, 53 checks, 2026-10-13; since 2026-10-28 the PIN scene expects the SIM PIN row and the APN section on their own, the verdict reading Waiting for the PIN): the Home WiFi step's offer only with a dongle that has a SIM, the no-home-WiFi path to the Review (None, the access point stays on, the dongle connected over the cable, the same address), the Reconnect over the access point (restarting, then back), the Checks with the dongle as the internet and the VPN line for Home Assistant, the bar and the header segment on `#/status` (once per dongle in the browser, OK hides it, a reload keeps it hidden), the dongle screen on its own (`#/espnetlink`: Save with nothing to change says so, no tiles, the bar hidden there), the PIN-locked SIM (the row, the field, Send PIN and save, the header says SIM PIN), the GPS nudge (the card, the row, one press ticks WiFi modem, the warning bar), Settings > USB's Set up button, the header for a GPS receiver, an Ethernet adapter and nothing. Run `probe_setup.mjs` (ALL PASS; four wording checks updated 2026-10-13: a dongle on the wire reads \"connected\") and `probe_espnetlink.mjs` after a change here
