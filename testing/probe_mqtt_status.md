# Status page MQTT row probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_mqtt_status.mjs`

Needs: PC, node + jsdom

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** The Status page's Network card, MQTT row, in three mock
scenes set through `window.__mockPreset` (`mqtt: {enabled}` for the
setting, `mqttConnected` for the status bit): enabled and the broker bit
down reads `Not connected`; enabled and the bit up reads `Connected`;
disabled reads `Off`. Before 2026-11-03 the row read `Off` whenever the
bit was down, which a user takes for a disabled setting: the Tailscale
subnet-route report ("mqtt status off on status page") was an unreachable
broker, not a switched-off MQTT. The page fetches `/api/settings/mqtt_manager`
once at its build for the word (the status bits carry no enabled flag).

**Records.**

- 2026-11-03: `MQTT STATUS PROBE PASS` (6 checks: the three words, no page
  error in any scene), on the preview built from the live components tree.

**Traps.** The probe reads the page's text for `MQTT <word>`; a second row
that starts with "MQTT" on the Status page would need a tighter selector.
