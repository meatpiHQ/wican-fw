#!/usr/bin/env python3
"""Wait until no app holds the OBD chip.

Since 2026-11-24 (meatpi-components pull request #14, GitHub issue #1078)
`POST /api/autopid/test` answers 409 while an app's monitor runs and in the
ten seconds after an app's last command, as can.send through the chip always
did:

    an app is using the OBD chip (a terminal, an ELM327 app): free 10 s after
    its last command, or when a monitor it opened ends (2 min at most)

A bench that used the chip as an app (the /ws/obd bridge, TCP 35000,
chip_nvm's snapshot) and then calls the test route is an app to the firmware
like any other. It waits here first: `client_idle_ms` on `GET /api/obd_chip`
at the yield time or above (4294967295 = no app ever wrote).

Found by `.\\test.ps1 multibus` on 2026-11-26: its chip snapshot ended 9 s
before its first Test call, which came back 409.

    import chip_free
    chip_free.wait("localhost:18081")       # host:port of the DUT's web server
"""
import json
import time
import urllib.request

YIELD_MS = 10000    # AP_CLIENT_YIELD_MS
MARGIN_MS = 500


def idle_ms(dut):
    """`client_idle_ms` of the DUT at `dut` (host:port), None when not read."""
    try:
        with urllib.request.urlopen("http://%s/api/obd_chip" % dut, timeout=6) as r:
            return json.loads(r.read().decode(errors="replace")).get("client_idle_ms")
    except (OSError, ValueError):
        return None


def wait(dut, limit=140.0):
    """Blocks until no app holds the chip. Returns the seconds waited, or
    None when the chip was still held after `limit` (an app's monitor is
    capped at two minutes) or the DUT did not answer."""
    t0 = time.time()
    while time.time() - t0 < limit:
        idle = idle_ms(dut)
        if idle is not None and idle >= YIELD_MS + MARGIN_MS:
            return time.time() - t0
        if idle is None:
            time.sleep(1.0)
        else:
            time.sleep(min(max((YIELD_MS + MARGIN_MS - idle) / 1000.0, 0.2), 2.0))
    return None
