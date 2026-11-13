# The CAN filters list of Automate, over the mock

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_can_filters.mjs` (and `python make_preview.py --min && node probe_can_filters.mjs`)

Needs: PC, node + jsdom

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

Ali's request of 2026-11-14: "allow users to add filters in vehicle specific
and in Custom pids". A filter here is a CAN filter of the autopid tables: a
frame the car sends by itself, read in a monitor window and decoded into
parameters. Until this date the page had no editor for one (a profile, the
DBC Signals page or Raw Config brought them). From the mock-up he chose a
separate CAN filters list under the PIDs of both tabs, the vehicle's filters
editable with more to add, the tab's switch covering its filters, Test for
a filter, and "Search" on the list's box in place of "Filter".

A filter of the vehicle's set carries `profile: true` (what a profile
brought and what is added on Vehicle Specific: the next profile replaces
it, as it replaces the vehicle-specific PIDs); every other filter is a
custom one. `name` is the page's own label on a row. Everything else a row
carries stays as stored.

The probe's table: one standard, one custom and one vehicle-specific PID,
the vehicle's filter SPEED on 0x284 and a custom filter on the 29-bit ID
0x18DAF110 with two parameters, one of them a multiplexed signal with a
class and a range. Four boots of the Automate page:

- **A** (Custom PIDs) the box says "Search by name, request or parameter";
  the CAN filters list shows the custom filter alone with its count, the ID
  in hex, the listen time and the column heads Name, CAN ID, Listen (ms),
  Parameters; the note no longer sends the user to Raw Config; the info
  button opens a bubble that explains a filter and names the tab's switch,
  and closes it again.
- **B** Add CAN filter sits beside Apply Configuration; it adds a row,
  opened, named NewFilter, with no ID yet (the field marked), 1000 ms
  listen and a 5000 ms cycle (the 4.x firmware's figures), one parameter
  reading B0; Unsaved edits shows; Test without an ID asks nothing and
  says why.
- **C** the ID field reads as the 4.x page did: `3b3` is hex (`0x3B3`), a
  plain `644` is decimal (`0x284`), junk is marked; the listen time is
  held to 50..60000; a parameter is edited, one is added and removed
  again.
- **D** Test sends the filter (the ID as a number, the listen time, the
  expressions, the mux fields only where a parameter has them); the dialog
  shows the exchange, the frame, its data as the expressions count it and
  the value; a 29-bit ID is shown with eight digits.
- **E** the search box finds a filter by a parameter's name and by its ID
  and says when nothing matches; Expand all and Collapse all take the
  filters along.
- **F** Apply: the vehicle's filter and the stored custom filter go byte
  for byte as they were (mux fields, class, range, no name added), the new
  one as edited and untagged, the PIDs untouched; a filter switched off
  goes as `enabled: false`; a removed one is gone; two new filters do not
  share a parameter name.
- **G** (Vehicle Specific) the vehicle's filter alone, under its
  parameter's name, with its live value; the bubble names the
  vehicle-specific switch and the profile; an edit and a new name go with
  the tag kept, a filter added there goes tagged `profile: true`, the custom
  filter untouched; an emptied name drops the key.
- **H** a car that does not send the frame (`__mockPreset.filterTest =
  "silent"`): the dialog says so in the exchange and beside it, with no
  arrival time, no frame data and no value.
- **I** Standard PIDs has no CAN filters list.
- **J** a filter in a shape the page does not know (the mock's own table:
  the ID as text, no parameters list) is shown, marked, and sent back as it
  was stored.
- **K** no page error in any boot.

The mock answers `POST /api/autopid/test` with a `filter` body as the
firmware does (`mock_api.js`).

## Records

**2026-11-14, later: `CAN FILTERS PROBE PASS`, 61 of 61, plain and
minified**, after the page took the 4.x firmware's ways (Ali: "check how
v4.50 used to handle the filters"): a plain number in the ID field is
decimal and `0x..` hex (it read everything as hex before), a new filter
listens for 1000 ms every 5000 ms (500 and 2000 before), the bubble no
longer asks for a 29-bit protocol (the firmware picks it), and the mock
answers a 29-bit ID with `ATTP7` ... `ATTP6` in its exchange.

**2026-11-14: `CAN FILTERS PROBE PASS`, 60 of 60, on the plain page and on
the minified one.** The same day on the minified page: `PROFILE FILTERS
PROBE PASS` 41 of 41 (its banner checks moved to the new wording), the
Automate probe, the row bus probe, the J1939 probe, the standard choice
probe (66 checks) and the smoke run (24 routes, 0 errors); on the plain
page the profile filters, Automate and row bus probes again.

## Traps

- The page patches `replaceChildren`, `append` and `prepend` to drop null
  children; a list built outside `h()` must not rely on a native call
  doing the same.
- The mock's default table holds a filter in a flat shape the firmware
  would refuse (leg J uses it on purpose); a probe that needs real filters
  presets `__mockPreset.autopidCfg`.
