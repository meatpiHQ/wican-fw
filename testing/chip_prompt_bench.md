# The OBD chip firmware prompt on the device

Command: `.\test.ps1 chipprompt` = `node tools/testbench/obd/chip_prompt_bench.mjs <out dir> --dut <dut ip> --legs prompt,banner,status` (by hand: `[--port 8089] [--fw22 <path>]`; the bench opens its own ssh tunnel to the DUT through rpi001)

Needs: the DUT on the bench AP, rpi001, Chrome and the Playwright devDependency of `tools/webui_preview` (`npm install` there once), the components tree for the V2.3.22 image (`MEATPI_COMPONENTS_PATH`, else the dev layout)

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The Quick Setup prompt of 2026-11-08 against the real chip. Before each leg
the chip is put on V2.3.22 the obdfw bench's way (the V2.3.22 image uploaded
as `/data/V2.3.99.txt`: the firmware's "the name is the version" contract),
the file deleted afterwards.

- **prompt**: the vehicle step opens with the popup (both versions, one
  button), Detect disabled with its chip, the banner behind; Update and
  continue; the progress popup with the records written while the API is
  seen running (and `GET /api/sleep` watched for `activity_why`); Done
  names the packaged version; Continue; the banner gone; Detect free once
  the acknowledgements are ticked; the chip on the packaged version, ready,
  not outdated.
- **banner**: a tap beside the popup closes it; the banner stays with
  Update now and Detect stays disabled with both acknowledgements ticked;
  Update now runs the same job to Done.
- **status** (Ali, 2026-11-08: a device set up before never re-runs the
  wizard): the Status page opens with the same popup and the banner
  `#chip-fw-banner`; Update and continue runs the job to Done; the banner
  is gone and stays away on the page's next refresh.
- the end: no new E line in the ring, no unexpected reset, no uploaded
  image left under `/data`, the chip on the packaged version (the bench
  never downgrades a chip: a unit found on V2.3.22 ends on V2.3.24).

Screenshots of the popup, the progress, Done and the step afterwards land in
the out dir (`test-reports/logs/<stamp>/chip_prompt/` from the runner).

## Records

- 2026-11-08 16:46 (unit 44bd8d73ca79 on `v6.0.0p-beta.7-14-g32d74a9-dirty`,
  chip found on V2.3.24): the **prompt leg PASS end to end on the real
  chip**: the V2.3.22 image written in 20 s (the chip back on V2.3.22,
  outdated), the step opened with the popup naming both versions, one
  button, Detect disabled with its chip, the banner behind; Update and
  continue; the progress popup read `111 of 3,557 records` within a second
  and the API was seen running on 30 polls up to 3557 of 3557; Done named
  V2.3.24 after 20 s; the banner gone, Detect free, the chip on V2.3.24
  ready and not outdated. The banner leg passed every step (the tap beside
  the popup, the banner with Update now, Detect disabled with both
  acknowledgements ticked, Update now to Done) and failed its last check
  only because the bench clicked the two acknowledgements a second time
  after Done (the re-render keeps them ticked, the click unticked them):
  a bench defect, fixed (`page.isChecked` first). 0 new E lines, 0
  unexpected resets, the file deleted
  (`test-reports/2026-11-08_1646_chipprompt.md`, the console lines in
  `test-reports/logs/chip_prompt_2026-11-08/run2_chipprompt_banner_tick.log`).
  No sleep countdown ran at 13.5 V, so `activity_why` stayed empty during
  the jobs (expected).
- 2026-11-08 16:49, the re-run after the fix: **CHIP PROMPT PASS**, 37
  checks, both legs, about 3 min (`test-reports/2026-11-08_1649_chipprompt.md`,
  the console lines in
  `test-reports/logs/chip_prompt_2026-11-08/run3_chipprompt_pass.log`, the
  screenshots under `test-reports/logs/2026-11-08_1649_chipprompt/chip_prompt/`).
  The unit afterwards: the chip on V2.3.24 and ready, no file under /data, 0
  E lines in the ring, 0 unexpected resets, the vehicle store untouched.
- 2026-11-08 17:03, the build with the Status page prompt (the same version
  string, the web UI changed): **CHIP PROMPT PASS**, 50 checks, the three
  legs prompt, banner and status, three chip writes, about 4 min
  (`test-reports/2026-11-08_1703_chipprompt.md`, the console lines in
  `test-reports/logs/chip_prompt_2026-11-08/run4_chipprompt_status_leg.log`,
  the screenshots under `test-reports/logs/2026-11-08_1703_chipprompt/chip_prompt/`).
  The status leg: the Status page opened with the popup naming both
  versions and the banner with Update now, the progress read `111 of 3,557
  records` within a second, 30 API polls running, Done after 20 s, the
  banner gone and still gone on the next refresh, the chip on V2.3.24. 0 new
  E lines, 0 unexpected resets.

## Traps

- The image goes up from the Pi (scp, then curl on rpi001 to the DUT): the
  first run of 2026-11-08 16:40 uploaded it from this PC through the tunnel
  with node's fetch and the route answered 500 on the 490 KB body (a tiny
  body went through; the cause was not chased), so both legs failed before
  the UI part and the end checks still passed (`test-reports/
  2026-11-08_1640_chipprompt.md`).
- Two chip writes of about 20 s each plus two fresh bring-ups: about 4 min.
- The sleep hold during the job (`activity_why: chip_update`) shows only
  while a countdown runs; on the bench at 13.5 V nothing counts, so the
  bench prints the reasons it saw and does not fail on their absence. The
  hold itself is proven on the mock and by the obdfw bench's sleep leg (the
  entry waits for the stream).
- The bench's tunnel port is 8089 by default: a stale ssh holding it leaves
  the device unreachable (`netstat -ano | findstr 8089`).
