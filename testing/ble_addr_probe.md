# Bluetooth address and unpaired-access probe: what an app sees before it pairs

Command: `python tools/testbench/ble/ble_pi_run.py ble_addr_probe_pi.py --dut 10.42.1.194 [--cycles 3] [--expect public|private]`

Needs: DUT on the bench AP through rpi001 (the UB500, bleak, the D-Bus passkey agent of `ble_bench.py`); Bluetooth may be off (the probe switches it on and back off, two planned restarts)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it is.** A measurement (prints `RESULT key=value` lines and `BLE ADDR PROBE DONE`)
that judges itself with `--expect public|private` since the fix of 2026-11-02 (then
`BLE ADDR PROBE PASS`: the address kept its kind and value across the cycles under
`public`, one address of type public that `GET /api/ble` `address` matches, or a new
random one per cycle under `private`; the plain link's subscribe refused (the CCCD
gate); the paired `ATI` answered by the chip through the factory `br_ble_obd`; no E
line of the Bluetooth tags). Written 2026-11-02 for the field reports of that day (the HA user's
"random MAC", Jeff's Discord thread: the advertising id changing per connection, FFF2
writes refused, no FFF1 notification); the assessment is
`internal/TASK_ble_address_pairing.md`. The probe removes the Pi's bond first (a bonded
BlueZ shows the identity address, never the on-air one), switches Bluetooth on with a
submit, then runs N connect-without-pairing cycles, each followed by a scan, and one paired
leg; Bluetooth is restored with a second submit. Per cycle on the plain link, in the order
every ELM app uses: subscribe FFF1, write `ATI\r` to FFF2 without response, wait 3 s for a
notification, then the secured accesses (a write with response, a Device Info read).
The paired leg: pair with the static passkey, subscribe FFF1, `ATI\r` with response, 5 s
for a reply. `dut_ble_bridges` names the bridges with a `ble` end, so a missing reply reads
right.

**What the values mean.** `distinct_addresses` = `cycles + 1` and `type=random` is the
rotating private address (every advertising restart takes a new one); 1 and `type=public`
is the fixed hardware address (what 4.x advertised). `unpaired_fff1_subscribe=ok` plus
`unpaired_fff2_write_no_response=accepted_by_client_stack` with `notified_bytes=0` and
`still_connected=True` is the silent drop (NimBLE refuses the ATT command before the access
callback, nothing is logged). A secured access on the plain link makes BlueZ start pairing
by itself; with no agent registered it fails and the link drops, so the later results of
that leg read `Not connected` / `Service Discovery has not been performed yet`: expected.
`paired_fff1_reply=b''` with no `ble` bridge is the v6 factory state.

**2026-11-02 13:05 and 13:20, firmware 6.0.0-beta.7 (HEAD), run 1 `--cycles 3`, run 2
`--cycles 1`:** 4 of 4 and 2 of 2 distinct random addresses; subscribe ok on the plain
link, the write-without-response dropped silently (0 bytes, link up), the write-with-response
refused (BlueZ paired, no agent, link dropped); paired: pair ok, subscribe ok, write ok, no
reply (no bridge). DUT restored, faults none. Report
`test-reports/2026-11-02_1305_ble_addr_probe.md`.

**Traps.** The DUT's station is suspended while the BLE client is attached
(`sta_ble_handover`): HTTP only before and after a leg. The crash ring's text carries ANSI
colour codes (`sed 's/\x1b\[[0-9;]*m//g'`), and `grep` wants `-a`. The Pi ends up bonded
again (the paired leg), which is the as-found state of this rig.

**2026-11-02 14:50 (the 4.x parity built, `internal/TASK_ble_address_pairing.md`, the final
image of the day, `--cycles 2 --expect public`): BLE ADDR PROBE PASS**: one address across
the two connections, 68:EE:8F:5A:65:3E type public, the same as `GET /api/ble` `address`
(the identity the Pi's bond had always shown); the plain link's FFF1 subscribe refused and
BlueZ's pairing attempt on it failed without the agent (the link dropped, the write legs
unreachable: expected); paired, FFF1 subscribed, `ATI` answered `ELM327 v2.3>`
through the factory `br_ble_obd` (`dut_ble_bridges=['br_ble_obd']`, no row added by the
bench); no E line of the Bluetooth tags. Output `test-reports/logs/ble_addr_probe_final.txt`.
