# J2534 over the USB cable: what the web page says (page probe)

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_j2534_usb.mjs`

Needs: PC, node + jsdom

The index row and the other stages: [../TESTING.md](../TESTING.md). The transport itself, on hardware: [j2534_serial_test.md](j2534_serial_test.md).

## What it covers

A contributor could not get J2534 to work over the USB cable (2026-11-20). The unit was not at fault: its USB connector ships in the Host role, where a PC sees only the board's console ports, and J2534 over the cable needs "Connector role" Device and "USB device class" CDC serial on the USB page. Nothing on the J2534 page said so, the role's help spoke of "a network adapter on a PC" alone, the role's own choice read "Device: network over the cable to a PC", and the device class had no help text.

The probe (prints `J2534 USB PROBE PASS`, 12 checks, ~9 s) loads the J2534 page and the USB page's settings over the mock API:

- the J2534 page has ONE small info button, in its header and outside the status block (the page itself gained no text: the two settings are not named outside the bubble);
- its bubble names the USB page and the two settings by the words that page uses, says what the PC then shows, and that the Host role's ports do not speak J2534;
- the status block repaints every 4 s: the probe keeps the bubble open across one repaint and the bubble still has its button (the rule of the UI's README for an info button on a page that repaints on a timer); Escape closes it;
- on the USB page the labels are "Connector role" and "USB device class", the choices are "Device: network or serial port to a PC" and "CDC serial", the role's help names the serial port beside the network adapter, and the device class has a help text (CDC serial is the COM port for a J2534 tool on the cable);
- no script error on either page.

## Records

**2026-11-20 22:43, the first version, the button beside the status chips: 11 of 11, and `node smoke.mjs`: `SMOKE PASS: 24 routes, 0 errors`.** Its first run was 10 of 11: the probe itself found the role's choice still reading "network over the cable to a PC", which the review of the page had missed; that text was changed too. On the unit (a build with that version, image `268e0ec3c9709523`, put on by upload): the page it serves carried the four texts.

**2026-11-20 23:15, the button moved into the page's header, and a check for it: `J2534 USB PROBE PASS`, 12 checks, on the plain preview and on the minified one (`--min`, the page as the firmware embeds it); `SMOKE PASS: 24 routes, 0 errors`.** The first version broke a rule the UI's own README states: the J2534 status block repaints every 4 s, and a repaint replaced the button under an open bubble. Against that version the new probe fails two checks (the button inside the status block; the open bubble without its button after a repaint). The firmware builds with the final page; that build was not put on the unit (the texts are the ones the unit served, the element that holds the button changed).

## Traps

- `preview.html` is built from the tree `MEATPI_COMPONENTS_PATH` names: without `--fresh` and that variable an older checkout's page is probed and the probe fails on texts that are there.
- The device serves its embedded page: a change to `index.html` shows on a unit only after a build with it is on the unit.
- The probe waits out one repaint of the status block (4.6 s); a mock that goes offline stops the repaints and that check fails on the block not having changed.
