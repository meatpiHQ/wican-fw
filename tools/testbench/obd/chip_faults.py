"""The OBD chip's device fault codes, for the benches that must not leave or miss one.

Since 2026-11-12 the firmware latches four codes about the chip in the device's
fault store (GET /api/faults; they stay until POST /api/faults/clear):

  obd_chip_woke      a nap found the chip awake after it had slept
  obd_chip_no_sleep  six re-sleeps in a row failed: the recovery reboot
  obd_chip_silent    the chip answered at neither UART speed and the bring-up
                     took its last resort, the reset line (latched also when
                     the chip came back)
  obd_chip_damaged   the chip runs but its stored identity reads as an error

A healthy chip latches none of them, whatever the device does: a bench that
ends with one has found something. One bench makes the firmware take the last
resort on purpose (obd_sleep_triggers_bench.py: a chip put to sleep by command
with a PowerEn value that wakes on a rising edge only, then a planned restart,
which gives the pin no edge): it expects `obd_chip_silent` and takes it away
again with clear_own(). The same chip woken by a real sleep's wake gets its
edge from the sleep pin and needs no last resort (obd_sleep_word_bench.py,
measured 2026-11-12): that bench expects no code at all.
"""

CODES = ("obd_chip_woke", "obd_chip_no_sleep", "obd_chip_silent", "obd_chip_damaged")


def by_code(body):
    """GET /api/faults as decoded JSON -> {code: fault}; {} for anything else"""
    if not isinstance(body, dict):
        return {}
    return {f.get("code"): f for f in body.get("faults", []) if isinstance(f, dict)}


def chip(faults):
    """of {code: fault}, the chip's own: {code: 'detail (count N)'}"""
    return {c: "%s (count %s)" % (f.get("detail"), f.get("count")) for c, f in faults.items() if c in CODES}


def clear_own(before, now, expected, post_clear):
    """Take away what the bench itself latched.

    before / now: {code: fault} as found and at the end; expected: the codes the
    bench latched on purpose; post_clear(): POST /api/faults/clear. The HTTP clear
    drops EVERY code, so it is sent only when nothing was latched as found and
    nothing but the expected codes is latched now: a fault the bench did not
    cause must not go with it. Returns (ok, sentence): ok is False when a code is
    latched that neither was there before nor is expected.
    """
    new = sorted(c for c in now if c not in before)
    strangers = [c for c in new if c not in expected]
    if strangers:
        return False, "latched during the run and not the bench's own: %s (left latched)" % ", ".join(strangers)
    if not new:
        return True, "nothing latched by the run"
    if before:
        return True, "%s latched by the run and LEFT, the store held %s as found (the clear drops every code)" % (
            ", ".join(new), ", ".join(sorted(before)))
    post_clear()
    return True, "%s latched by the run and cleared (the store was empty as found)" % ", ".join(new)
