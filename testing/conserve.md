# Counter conservation (CAN, USB-NCM, WiFi)

Command: `.\test.ps1 conserve`. Its CAN part alone: `python tools\testbench\can\can_conservation_test.py --dut <the DUT's address on the Pi's network> --pcan <the PCAN channel on the DUT's bus> [--only tiers|flash] [--flash-frames 20000]` (PC; the bench asks the DUT through `ssh rpi001 curl`).

Needs: PCAN on the DUT's bus (`WICAN_PCAN_DUT`, the adapter's plugs may be swapped) + rpi001 + a quiet bus. AutoPID the bench switches off itself for its run and puts back after it (two planned restarts when it is on as found, none otherwise); nothing may be broadcast by the simulator: the bench checks for three seconds and fails `bus_quiet` otherwise. Prints `CAN CONSERVATION PASS`, about 1 min for the CAN part, 2.5 min with the two restarts. The USB-NCM part needs the DUT in the device role and is skipped without it.

The index row and the other stages: [../TESTING.md](../TESTING.md). The same idea for J1939: `tools/testbench/can/j1939_conservation_test.py`. The line rate through ISO-TP: [j2534fc.md](j2534fc.md), legs N and W.

## What the CAN part covers

Exactly N frames are put on the wire by the PCAN and the DUT's `/api/can` counters must account for every one: `rx` up by N, `rx_missed` and `dispatch_drops` unchanged (the standard's performance-truth rules: a counter delta that is a multiple of the workload is a bug, and "plausible traffic" is not a pass).

| Tier | What is sent | What is asserted |
|---|---|---|
| 10% | 5000 frames, one every 2 ms | `rx` +5000, nothing missed, nothing dropped |
| 50% | 5000 frames, one every 250 us | the same |
| 100% | 5000 frames back to back | the same |
| flash (2026-11-17) | frames back to back for as long as three files of 32 KB take to upload to the unit's `/data`, at least 20,000 | the flash was written during the burst (the three uploads answered `ok`, the erase counter of `health.flash` moved), and `rx` up by exactly what was sent, nothing missed, nothing dropped |

The flash tier is the unit's own flash against its own bus: an erase stops every task for its length and leaves the receive interrupt running, so the frames of that time wait in the CAN node's receive queue, and a queue that is too short loses them (`rx_missed`). The tier keeps handing frames to the adapter until the last upload is answered; the adapter queues tens of thousands and the wire trails the hand-over by seconds, which puts every upload inside the burst whatever each one takes. An upload through `/api/fs/upload` is erased and written on the flash, and tells the configuration backup to refresh its copies, which is one more write.

## Records

**2026-11-17, the flash tier on the 6.0.0-beta.11 release image (the receive queue at 64 frames; bench unit 14c19f457fd9, PCAN channel 2): `CAN CONSERVATION FAIL`.** 40,000 frames at the line rate, three uploads inside the burst, the flash +207 writes and +27 erases: `rx` +39,447, `rx_missed` +553. A first run the same hour lost 489 of 40,000. That is about 20 frames an erase beyond the 64 the queue held: an erase holds the RX task about 23 ms, 90 frames at this rate.

**2026-11-17, the receive queue at 256 frames (components `can_core_driver.c`, uncommitted): the flash tier five times with nothing lost.** 40,000 of 40,000 (30 erases), 60,000 of 60,000 twice (27 and 28 erases), then with the tier running until the uploads are done 85,585 of 85,585 (27 erases) and 69,192 of 69,192 (28 erases): `rx_missed` +0, `dispatch_drops` +0 every time. Three of the five ended `CAN CONSERVATION PASS`; the other two failed only the bench's own check that every upload fell inside the burst, which the first trap below describes and the later version of the tier removes.

**2026-11-17, through `.\test.ps1 conserve` on the build with the three queues at 256 (the receive queue, the J2534 channel's, the ISO-TP provider's): `CAN CONSERVATION PASS`.** The three tiers 5000 of 5000 each, the flash tier 56,530 of 56,530 with 27 erases, nothing missed, nothing dropped. USB-NCM skipped (the DUT not in the device role), `WIFI CONSERVATION PASS`. The CAN part again on the build of the final sources: the three tiers, and the flash tier 58,070 of 58,070 with 28 erases.

**2026-11-18 20:20 and 20:23, the bench switching AutoPID off for its run (Ali's decision that evening; the unit as found has AutoPID on), the final build of that day (ELF `eb50e90bb22b268f`): `CAN CONSERVATION PASS` twice.** The three tiers 5000 of 5000 each in both runs, the flash tier 56,780 of 56,780 (27 erases) and 57,947 of 57,947 (28 erases), `autopid_back_as_found` green, the unit's restart count up by two a run and no unexpected reset. `WIFI CONSERVATION PASS` both times, USB-NCM skipped (the DUT not in the device role). Until then the CAN part was red on a unit as found: `bus_quiet` and every tier counted AutoPID's requests and their answers (5,060 and 5,093 for 5,000 that afternoon).

The first run with the change, at 20:06 straight behind the `j2534std` stage, failed its first tier with 3 frames too many (5,003 for 5,000, nothing missed, nothing dropped, the other tiers exact). Not found again: the same tier three times by hand after the same restart, with every frame the simulator received read from its log meanwhile (only the bench's), then the two stage runs above. Where those three frames came from is not known; the simulator's own transmissions are in no log.

## Traps

- `bus.send()` of python-can returns when the adapter's driver has the frame, not when the frame is on the wire: 40,000 frames are "sent" in two seconds and take ten to leave. A first version of the flash tier timed its uploads against the send loop and found none of them "inside the burst". The tier now ends its hand-over on the uploads and reads the unit's own `rx` until it stands still.
- Until 2026-11-17 the bench took the newest lease on the Pi as the DUT and `PCAN_USBBUS1` as the bus. Both are arguments now and the stage passes its own (`-DeviceId`, `WICAN_PCAN_DUT`).
- The bus must be quiet: with AutoPID on, the OBD chip's requests and the simulator's answers are counted in `rx` too. The bench switches AutoPID off for its run since 2026-11-18 and puts the settings back in a `finally`, so a run that dies in a tier still restores them; it then waits until the unit has been up 30 s (the configuration copies are refreshed 20 s after a boot that follows a settings change). A run killed from outside leaves AutoPID off: switch it on again on the AutoPID page.
- The flash tier leaves nothing behind (it deletes its file), but each upload makes the configuration backup refresh its copies a little later: a stage that runs straight after it and needs the flash at rest has to wait for that (see `wait_flash_at_rest()` in the flow control bench).
