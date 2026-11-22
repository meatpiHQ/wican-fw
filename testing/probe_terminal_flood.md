# The Terminal page under a monitor on a busy bus (page probe, in Chrome)

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_terminal_flood.mjs`

Needs: PC, node + playwright + Chrome

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

`ATMA` in the ELM327 terminal on a busy bus froze the browser tab (GitHub issue #1085, a 2012 Chevrolet Volt's HS bus at about 2,800 frames a second): the page appended every WebSocket message as a text node of its own and asked for the scroll height right after, a layout per message, and it kept everything it ever received. Since components pull request 12 (overkillthemighty) the output is one text node, written at most once a screen frame and capped at the last 200,000 characters, cut at a line behind an `[earlier output dropped]` line; what waits for the next frame is capped the same way, since a hidden tab draws no frames.

A freeze is a matter of layout, which jsdom does not do, so this probe drives the page in Chrome (playwright, as `can_filters_page.mjs` does). The page is `preview.html` (the real `index.html` over the mock API); the probe takes the mock's `/ws/obd` socket in hand and feeds it one frame line a message at a fixed rate (2,800 a second for 20 s by default: `--rate`, `--secs`; `--page <file>` probes another build of the page).

The probe (prints `TERMINAL FLOOD PROBE PASS`, 10 checks, about 30 s):

- the ELM327 terminal connects and says so;
- once a second the probe asks the page a question from outside: the worst answer of the run stays under 500 ms;
- the page took the messages as they came (98 % of those due), and its own 50 ms timer never stalled for 500 ms;
- the output element holds one text node however much arrived, at most the last 200,000 characters, with the line that says output was dropped first; the first line kept and the last one are whole frames;
- 50,000 lines with no screen frame given to the page (a hidden tab): the cap holds, and the newest line is there when a frame comes;
- a command typed afterwards goes to the socket and shows in the output; no script error.

## Records

**2026-11-22, the page of components `main` before the change (`79caf32`): FAIL, 7 of 8 checks reached.** The worst answer 16.4 s (two answers in the whole run), 7,041 of 156,146 due messages taken in 55.8 s, the page's timer stalled for 19.1 s, 7,044 nodes in the output element and 218,292 characters, still growing; the step without screen frames did not come back within 60 s. The same numbers from the first measurement that day, with another script: 19.3 s, 7,103 of 174,545, a stall of 22.4 s.

**2026-11-22, the page with pull request 12 (the live components tree, `preview.html` rebuilt): `TERMINAL FLOOD PROBE PASS`, 10 checks.** The worst of 20 answers 27 ms (median 14 ms), 56,758 of 56,829 due messages taken in 20.3 s, the longest stall of the page's timer 78 ms, 3 nodes, 200,007 characters with the marker first, whole lines at both ends; with no screen frame for 50,000 lines 200,017 characters and the newest line last; `ATRV` typed afterwards went to the socket and showed.

## Traps

- `preview.html` is built from the tree `MEATPI_COMPONENTS_PATH` names: without `--fresh` and that variable an older checkout's page is probed.
- A page that freezes keeps its browser from closing. The probe does not wait for that: it prints its verdict and ends, and a Chrome it started may stay behind for a moment.
- The rate is the probe's, one message a line. What a unit puts into one WebSocket message depends on how the firmware cuts the chip's output; the car's own test (the pull request's record) is 63 s of `ATMA` at about 2,800 frames a second on the unit's page.
- The device serves its embedded page: a change to `index.html` shows on a unit only after a build with it is on the unit.
