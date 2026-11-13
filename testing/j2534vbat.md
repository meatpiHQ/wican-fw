# J2534 measured battery voltage

Command: `.\test.ps1 j2534vbat`. Expected: `J2534 VOLTAGE PASS`, about 45 s.
Use `-Port`, `-DutIp` and `-DeviceId` for the connected bench unit.
`.\test.ps1 j2534vbat driver -SkipBenchCheck` runs only the fake-device checks.
Index: [../TESTING.md](../TESTING.md).

Needs the ECU sim v2 OBD pin 16 supply feeding the WiCAN, rpi001 for the
HTTP/J2534 tunnel, the 32-bit Python at `C:\Users\Ali\idf_tmp\py32\python.exe`,
and a 32-bit WiCAN DLL. No CAN messages or PCAN adapter are needed.
Set `WICAN_J2534_DLL` to an absolute DLL path to test a build before installation.
The driver repo's `.\build.ps1` builds it without changing the installed driver.

## Coverage

- A fake TCP device checks that the DLL really sends IOCTL 0x03 on channel 0,
  returns 13,890 mV, rejects a missing result from old firmware, and propagates
  an unavailable-ADC error. Errors leave the output untouched. A null output
  and an invalid device id (99) are refused. An id in the channel range (2)
  is answered like the device id: tools that pass their channel id there
  were answered by every earlier driver and the reading is device-wide, so
  each fake-device case sees the request twice on the wire.
- The DUT is configured with J2534 enabled on port 6809 and exposed to the LAN.
  Only that settings document changes. One planned restart applies it.
- At 13.5 V and 14.5 V, the supply and battery API must settle within 150 mV.
  The wire reply must be exactly an ACK with 8 payload bytes, the matching
  sequence/channel, NOERROR and a millivolt value within 100 mV of the API.
  The DLL must return the same measured value. No CAN channel is opened.
- READ_VBATT before OPEN and after CLOSE must return only
  ERR_DEVICE_NOT_CONNECTED, not a success-shaped response.
- Zero error logs and no new faults. The supply setpoint and whole settings
  document are restored in nested finally blocks, even on a failed leg.
  Only the two planned settings restarts may occur.

Host half: `.\test.ps1 host j2534_server`, 32 Unity tests. The voltage cases
run the production helper with a stubbed battery owner: rounding, valid zero,
closed session, unavailable reading and invalid float values, and the
byte-exact request and replies of the wire protocol's section 3.

## Records

- 2026-11-13, driver reproduction: the installed DLL returned 12,000 mV for
  every fake-device case, accepted a null output, and sent no READ_VBATT request.
  `2026-11-13_1544_j2534vbat_driver.md`: expected FAIL.
- The fixed driver passed all three fake-device cases:
  `2026-11-13_1545_j2534vbat_driver.md`.
- Firmware reproduction on beta.9: supply 13.558/14.554 V, battery API
  13.60/14.61 V; both wire replies were `00 00 00 00`, status only.
  Before OPEN and after CLOSE also incorrectly succeeded. The fixed DLL
  refused these missing results instead of substituting a voltage.
  `2026-11-13_1552_j2534vbat.md`: expected FAIL; settings/supply restored,
  two planned restarts, zero errors/faults.
- Host tests: 31 PASS, `2026-11-13_1554_host_j2534_server.md`.
- Fixed firmware and DLL: `2026-11-13_1615_j2534vbat.md`, PASS. The DLL
  returned 13,594 mV and 14,606 mV at measured supply 13.558/14.554 V.
  Both wire responses carried exactly eight payload bytes; unopened/closed
  devices were refused. Two planned restarts, zero errors/new faults, supply
  and settings restored.
- Existing J2534 behavior: all 28 checks PASS with the rebuilt DLL,
  `2026-11-13_1618_j2534std.md`. The observer is PCAN_USBBUS2 on this rig;
  the preceding run on PCAN_USBBUS1 missed only its three physical-bus
  assertions while all tool requests answered.
- 2026-11-13 17:50, the review of that day's work: the first cut of the DLL
  refused every id but 1 for READ_VBATT. Driver 1.6 and earlier answered
  any id, and tools that pass their channel id there exist, so a second
  channel's id would have gone from "12.0 V" to `ERR_INVALID_DEVICE_ID`. An
  id in the channel range is answered now, 99 is still refused; the
  fake-device cases check both. `2026-11-13_1750_j2534vbat_driver.md`, PASS.
- **2026-11-13 19:40: J2534 VOLTAGE PASS** on the firmware and the DLL as
  committed (ELF `6da5ee5883b2fde8`), 26.7 s: supply 13.557 V, battery API
  13.590 V, the wire ACK `00 00 00 00 13 35 00 00` (13,587 mV), the DLL
  13,587 mV for the device id and for a channel id; supply 14.554 V, API
  14.600 V, wire and DLL 14,604 mV. Refused before OPEN and after CLOSE,
  zero error logs, no new fault, the supply back at 13.802 V, the settings
  document as found, two planned restarts. `2026-11-13_1940_j2534vbat.md`.
- 2026-11-13 20:36: PASS again with the server's RX pump in its own file
  (ELF `1d15a0a81feae3a7`): 13.558 V and 14.554 V of supply, the DLL within
  100 mV of the battery API both times. `2026-11-13_2036_j2534vbat.md`.

## Traps

The bundled 1.6 installer still contains the nominal-voltage implementation;
firmware alone cannot change a value fabricated in that DLL. Both sides must
be updated. Never waive the missing-result check for compatibility.
The battery owner caches its calibrated, averaged samples; wait for the
sample to catch up with a supply change instead of comparing immediately.
Run this and all other DUT-changing stages serially, especially `stackaudit`:
it can submit settings and restart the device for its ephemeral-task leg.
