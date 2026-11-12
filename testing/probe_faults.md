# Fault codes card and its info bubbles probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_faults.mjs`

Needs: nothing but Node and the preview (no device). The index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers** (prints `FAULTS PROBE PASS`, 23 checks). System Monitor's card "Fault codes" over the mock's `/api/faults` with a probe's own list (`__mockPreset.faults`: two OBD chip codes and one code the UI has no words for):

- The table is the one it was: the five columns Code, Detail, Count, First seen, Last seen and no sixth; one row per code; the code and the detail as the device sent them; the header's chip counts them. Ali, 2026-11-12, on a mock-up that put a title, a paragraph and a severity on every row: "has too much information, I prefer simple and clean design, if we want to add additional info add a bubble".
- The bubble's button: beside a code that has an entry in the UI's `FAULT_INFO`, in the Code cell, labelled for a screen reader; none beside the code the UI does not know; no bubble before a click.
- One bubble: a click opens it with a title and a paragraph and the button says it is open; the next row's button swaps it (still one).
- The page's refresh leaves it alone: two refreshes (the page polls every 2 s) later the bubble is still there and the card was not painted again (the same button element). Before the change the card was repainted at every refresh.
- Three ways to close: the same button, Escape, a click elsewhere; a click inside the bubble does not close it.
- `Clear fault codes` as before: the confirm, then "No fault codes, device healthy." and no bubble left behind.
- No jsdom error.

## Records

- 2026-11-12, the first run with the change: `FAULTS PROBE PASS` (23 checks); `DIAG PROBE PASS` beside it. One red in between was the probe's own: the confirm dialog's button reads `Continue` (the second argument of `confirmModal` is the title).
- 2026-11-12, by eye: two screenshots of the preview with a bubble open (1320 px and 390 px wide, `test-reports/logs/fault_bubble_20261112/`, taken with `shoot_faults.mjs` there).
- 2026-11-12 17:20, on a device (the bench unit whose OBD chip is dead, the image of that minute, a real browser through the tunnel, `shoot_device.mjs` in the same folder): the five columns, the unit's own row `obd_chip_silent` / `silent; still silent after the reset pulse`, the bubble "The OBD chip did not answer" open and still there after a refresh, the header chip `1 fault`, no page error.

## Traps

- The bubble is appended to the body and fixed to the viewport. Look for `.ibubble` in the document, not in the card.
- jsdom has no layout: the probe cannot say where the bubble sits. The screenshots do.
