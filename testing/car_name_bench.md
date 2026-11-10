# The car's name in the header, on the device

Command: `.\test.ps1 carname` (the bench unit: `-DeviceId 44bd8d73ca79`)

Needs: DUT on the bench AP (through rpi001) with a current car in its vehicle store, Chrome, the Playwright devDependency of tools/webui_preview

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The screens of [probe_car_name.md](probe_car_name.md) against the device:
`tools/testbench/system/car_name_bench.mjs` opens the Status page in Chrome
through its own tunnel (ssh -L through rpi001).

- **show** the header shows the stored name of the current car, the pill in
  its two-line form inside the 58 px header with the id and the address
  under the name, the tab title `<name> · WiCAN Pro`, the `not set up` chip
  as the store's `pending_profile` says.
- **rename** the popover holds the stored name; Set name shows the new name
  as not saved and one change on Submit, and sends NOTHING: four seconds
  later the store still has the old name and the boot count stands; Submit
  says it restarts; Continue: the boot count moves by one, the record is a
  planned restart (`user_request` from `web_ui`), the store and, after a
  reload, the header and the tab carry the new name.
- **restore** the name as found goes back the same way.

Afterwards: the stored name as found, no unexpected reset, no E line in the
ring, no JS error on the page. Two planned restarts. A run that dies midway
puts the name back over the API.

## Records

- 2026-11-10 11:03: `CAR NAME PASS` (27 checks, 32 s, two planned restarts)
  on the bench unit 44bd8d73ca79, the build of this change flashed over
  6.0.0-beta.9 (the version string still reads 6.0.0-beta.9), the car as
  found `1WC 0001` (the simulator's, setup not finished: the chip shows).
  Report `test-reports/2026-11-10_1103_carname.md`.

## Traps

- A flash wipes the restart history: the boot count starts at 1 again.
- The rename alone changes no setting, so `POST /api/settings/submit`
  answers `reboot:false` and the restart is the page's own
  `POST /api/restart`: its record reads `user_request`, not `config_apply`.
- A device with no current car fails the bench at its second check: detect
  a car first (Quick Setup's vehicle step).
