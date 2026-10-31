# The quick bench check

Command: `.\test.ps1 check`

Needs: PC + rpi001

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The quick bench check**: only the bench preflight below (~10 s), then `RESULT: PASS/FAIL`. Run it after re-plugging anything: it names the console port it found (`serial=COM254 (CH344 B)`), the DUT address it resolved, and the FTDI (PSU) / `MeatPi USB-CAN` (ECU sim) ports, and since 2026-10-27 which radio serves WICAN_TEST_AP (`ap=P4 (wican-bench-usb)`; a Pi hotspot twin beside the P4 fails the preflight with the parking command)
