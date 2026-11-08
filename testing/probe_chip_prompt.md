# Quick Setup's OBD chip firmware prompt probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_chip_prompt.mjs` (and `python make_preview.py --min && node probe_chip_prompt.mjs`)

Needs: PC, node + jsdom

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The finding of 2026-11-08 (`internal/OBD_CHIP_ATWS_AUTO_SEARCH_2026-11-08.md`):
on chip firmware V2.3.22 the warm start the bring-up sends kills the chip's
automatic protocol search until a power cycle, so the vehicle detection and
every app's `ATSP0` fail to find the car; V2.3.24 does not have the fault.
Ali's fix: the vehicle step of Quick Setup asks for the update first. The
probe boots `#/setup/vehicle` four times over the mock (`__mockPreset.diag`:
`chip22`, `updateMs`, `updateFail`; `POST /api/obd_chip/update` starts a job
that `GET /api/obd_chip` animates):

- **A** the popup names the fault and both versions, one button (Update and
  continue), the banner behind carries the same offer, Detect my vehicle is
  disabled with the chip `Update the chip first`; the button posts the
  update once and opens the progress popup (the records written, the three
  steps) that a tap beside it does not close; Done names the new version;
  Continue leaves no popup, no banner, no chip; Detect is free once the two
  acknowledgements are ticked; no JS error.
- **B** a tap beside the popup closes it without a post; the banner stays
  with Update now; Detect stays disabled with both acknowledgements ticked.
- **C** the job ends with `ESP_ERR_TIMEOUT` at record 1248: the banner turns
  red with the error and the record, Try again is offered and runs the job
  again; the second failure adds the advice to unplug the device.
- **D** a chip on the packaged version: no popup, no banner, Detect as
  before.
- **E** the Status page (`#/status`; Ali, 2026-11-08: a device set up before
  never re-runs the wizard): the same popup opens, the banner
  `#chip-fw-banner` with Update now; a tap beside the popup closes it and
  the banner stays; the page's next refresh (4 s) does not open the popup
  again (once per browser session, `sessionStorage`); Update now runs the
  progress popup (the banner reads Updating meanwhile), Done, Continue, the
  banner gone and still gone on the next refresh.
- **F** the Status page with a current chip: no popup, no banner.

The flow is one helper in index.html (`chipFw`: prompt, banner, run, done)
that the wizard chunk and the Status page share; the popup CSS lives in the
page since the Status page shows it too.

## Records

- 2026-11-08: `CHIP PROMPT PROBE PASS` plain (27 checks, scenes A to D) and
  with `make_preview.py --min`; later the same day with the Status page
  scenes E and F (40 checks), plain and minified
  (`test-reports/logs/chip_prompt_2026-11-08/probes_round2.log`).

## Traps

- The page polls the job once a second: a mock job shorter than that never
  shows as `running`; the page then judges the job by its result changing
  against the state before the post (`lines`, `last`, `done`), which is
  what scene C relies on with `updateMs: 1500`.
- The popup is the page's modal: `#modal-root .acts button`; a tap beside it
  is a click dispatched on `#modal-root` itself (the root's own handler
  closes on `e.target === root`), which the progress popup disables.
- `probe_setup.mjs`, `probe_j1939.mjs` and `probe_wwh.mjs` walk the vehicle
  step too, on a chip at the packaged version: re-run them after any change
  to the prompt.
