# The car's name in the header probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_car_name.mjs` (and `python make_preview.py --min && node probe_car_name.mjs`)

Needs: PC, node + jsdom

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

A user's request of 2026-11-10: the car's name at the top of every page. The
name is the vehicle store's `name` of the current car (Quick Setup's Name
field), which the status poll already carries as `vehicle.name`; the page
shows it as a first line inside the header pill, above the id and the
address, and on the browser tab. A rename from the header is staged into
Submit Changes and restarts the device like a setting (Ali, 2026-11-10: one
write of the store file per submit, not one per edit). The probe boots the
Status page four times over the mock (`__mockPreset.vehicles`):

- **A** a named current car: the name in the pill, the pill in its two-line
  form with the id and the address under the name, the tab title
  `<name> · WiCAN Pro`; a click opens the popover (the field with 31
  characters at most, the count, the VIN, the stored cars, Cancel then Set
  name at the right edge); an empty name cannot be set; Escape and a click
  beside the popover close it with nothing staged; Set name stages the
  rename: the header shows the new name as not saved yet, the Submit button
  counts one change, NO request has gone out, the tab keeps the stored
  name, a status poll does not paint the stored name back; the popover
  reopens on the staged name; the stored name typed back unstages it; Enter
  sets a trimmed name; Submit asks first, then sends ONE
  `PUT /api/autopid/vehicles/<key>` with `{"name"}` and ONE
  `POST /api/restart`, and the page waits for the device.
- **B** no current car: no name, the pill in its one-line form, the tab
  title `WiCAN Pro`.
- **C** a current car whose setup never finished: the name with its
  `not set up` chip.
- **D** a staged rename is dropped by Discard on the unsaved-changes dialog:
  the stored name is back, nothing was sent.

## Records

- 2026-11-10: `CAR NAME PROBE PASS` (43 checks) plain and with
  `make_preview.py --min`; smoke 24 routes 0 errors, and the newcar, setup,
  reconnect, automate, wifi_mode, backup and destinations probes green on
  the same page.

## Traps

- The mock counts restarts in `__mockState.restarts` and renames in
  `__mockState.vehiclePuts`; the mock's submit answers `reboot:false`, as
  the device does for a submit that changed no setting, so the restart the
  probe counts is the page's own.
- The popover closes on a `mousedown` outside it, not on a click.
- The page's status poll runs every 3 s: scene A waits 3.5 s to prove the
  staged name survives it.
