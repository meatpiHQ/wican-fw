# The crash on the Status page, and the safe mode page

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_crash_note.mjs && node probe_safemode.mjs`

Needs: PC, node + jsdom

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The crash on the Status page, and the safe mode page** (2026-10-05; prints `ALL PASS`, 31 checks, then `SAFE MODE PAGE PASS`, 14 checks). `probe_crash_note.mjs` over the mock: no `Last crash` row without a note; the row's words for an exception, an abort, a watchdog, an unfinished note; `Details` (the note as JSON, `Copy details`); after a power cut the row is the STORED report with its date and `Report` opens the firmware's own text (`GET /api/restart/report`) with `Copy report`, `Download`, `Clear`; Clear asks first and sends one `DELETE`; the same crash in RAM and in flash is one row with both links, a different stored crash gets its own `Stored crash report` row; a start after a crash park reads `Start after a crash park · button`; the restart history marks a boot that parked; no long dash. `probe_safemode.mjs` runs `main/web/safemode.html` itself (the page is embedded into the firmware as it is, and no bench can reach safe mode: it needs the button and serves its own access point) against the three routes it calls: the `Crash Report` section comes first and shows the device's text, `Download Report` links `/crash_report.txt`, `Copy Report` works without the clipboard API (plain http), a device with no report says so, a failed request leaves the page usable, and Factory Reset and Firmware Update still post what they always did
