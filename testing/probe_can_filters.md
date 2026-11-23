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
class and a range. Six boots of the Automate page (four until 2026-11-23):

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
- **J** a filter in a shape the page does not know (the ID as text under
  `id`, no parameters list: a hand-made file can bring one, no device
  stores it) is shown and marked. It has no ID the device takes, so Apply
  stops at it (as in L). With its ID typed it goes with everything it
  carried, the ID as a number beside it.
- **K** no page error in any boot.

Since 2026-11-23, Apply with a CAN filter that has no ID. A user's report:
on Standard PIDs, with seven scanned PIDs ticked, Apply Configuration
answered "Rejected: filter 0: frame_id required", and the same PIDs added
one by one after a reload went through. The cause, reproduced on the bench
unit's own page (below): one click on Add CAN filter on another tab leaves
a row with no ID in the page's tables, Apply sends the tables of every tab,
and the device refuses them whole and names the filter by its position.

- **L** Add CAN filter on Custom PIDs, then Apply on Standard PIDs:
  nothing is sent; Custom PIDs opens with the row open, its ID field marked
  and the cursor in it; the line under the list reads "Not sent: the CAN
  filter NewFilter has no CAN ID. Enter the ID of the frame it reads, or
  remove the filter, then Apply Configuration again."; the toast names the
  filter and its tab; no "Rejected" line, the edits still unsaved. With an
  ID typed the line goes and Apply on Standard PIDs sends the table. A
  second such filter stops Apply again; removed, the line goes and the
  table is sent without it.
- **M** Apply pressed on the filter's own tab, with a search that hides the
  row: nothing is sent, the row is shown and open with the cursor in its ID
  field, and the pane is not built again (the same list and the same search
  box stay on the page).
- **N** a filter added on Vehicle Specific: Vehicle Specific opens, the
  toast names that tab, the line names the new filter and not the vehicle's
  stored one.
- **O** Raw Config sends the JSON as typed: the table with the filter that
  has no ID goes to the device, whose own words come back in the toast
  ("Config rejected: filter 2: frame_id required"), and the tab stays.

The mock answers `POST /api/autopid/test` with a `filter` body as the
firmware does (`mock_api.js`). Since 2026-11-23 it also refuses a table
with a filter whose `frame_id` is not a number from 1 up, with the
firmware's words (`filter N: frame_id required`); until then it took such
a table, and leg J expected the page to send one.

## Records

**2026-11-23: `CAN FILTERS PROBE PASS`, 86 of 86, on the plain page and on
the minified one**, with Apply stopped at a CAN filter that has no ID (legs
J reworked, L to O new) and the mock refusing such a table as the firmware
does. The same probe on the page as it was before the change (the
`web_ui_v2` tree of components `a823199`, the stricter mock): red, with the
reporter's words. Leg L: Apply on Standard PIDs sent the table (1 PUT), the
tab stayed, the line under the Standard PIDs table read "Rejected: filter
0: frame_id required" and the toast "Config rejected: filter 0: frame_id
required"; leg J's Apply sent the flat row; the probe then stops at the row
it expects on the page. Before the change, with the mock as it was: 61 of
61, leg J having the page send a filter without an ID and the mock take
it. The other probes that touch the tables were run before and after the
change, green both times: profile filters 41 of 41 (its hand-made filter
now in the stored shape), Automate, the dashboard and its layout, the
standard choice (66), the row bus, J1939, WWH, Quick Setup, destinations,
the new car (14), profile first (47); after it the smoke run too (24
routes, 0 errors), and on the minified page profile filters and Automate.
On the device: `.\test.ps1 canfilters`, 64 checks, its page leg p6
([canfilters.md](canfilters.md), with the reproduction on the unit's own
page before and after).

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
- Until 2026-11-23 the mock's default table held a filter in a flat shape
  the firmware refuses, and the mock took any table back. No probe could
  see what a filter without an ID does on a device, and leg J had the page
  send one on purpose. The default filter is in the stored shape now
  (`frame_id` 0x18DAF110, one parameter SOC_BMS), the mock refuses as the
  firmware does, and leg J presets its flat row itself.
- A mock that is kinder than the firmware hides the page's faults: when a
  route of the mock accepts a body, check that the firmware accepts it too.
