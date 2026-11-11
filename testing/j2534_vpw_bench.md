# J2534 J1850 VPW (GM Class 2)

Command: `.\test.ps1 j2534vpw` = `python tools\testbench\usb\j2534_vpw_bench.py localhost --port 18534 --rig localhost:18081` (PC; two ssh forwards through rpi001: 18081 to the DUT's port 80, 18534 to its J2534 port 6809; ECU sim v2 at its own address)

Needs: main fw on DUT + rpi001 + ECU sim v2. Prints `J2534 VPW PASS`. About 1.5 min: two planned restarts of the unit when its J2534 server is off as found, two simulator restarts, no write of the OBD chip.

Without the rig: `python tools/testbench/usb/j2534_vpw_bench.py <dut_ip> [--port 6809]` (on rpi001, or through a tunnel) runs the same exchange against whatever is on the bus, with `j2534_server` enabled and `allow_lan` set by hand: `J2534 VPW PASS` when a J1850 node answers, `J2534 VPW SKIP (...)` when connect, init and write worked and nothing answered, `J2534 VPW FAIL` only on a real defect.

The index row and the other stages: [../TESTING.md](../TESTING.md). Where the defect was reproduced first: [protocol_baseline_bench.md](protocol_baseline_bench.md).

## What it covers

VPW rides the OBD chip, not the native CAN controller (2026-10-19, modelled on the nightjoker7 4.47p fork, rebuilt on the v6 `obd_chip` claim model and the chip's own `VTFullyRequestCk` USDT). CONNECT runs the VPW init on the chip, a J1850 message is `[3-byte header][payload]` whole (no CAN-id prefix), WRITE sets the header (`ATSH`, cached) and sends the payload, and the chip's reply lines come back as RX messages through the channel's PASS / BLOCK filters, which are applied in software.

| Step | What is done | Must read |
|---|---|---|
| as found | the unit's `j2534_server` settings, `GET /api/obd_chip`, the chip memory snapshot (`chip_nvm`), the restart history | the unit answers, the chip ready |
| the rig | ECU sim v2 on VPW alone (`bench_bus.sim_only("vpw")`, one simulator restart); `j2534_server` enabled with `allow_lan` when it is not (a planned restart) | the simulator's status lists `vpw` and nothing else |
| the session | HELLO, OPEN, CONNECT J1850VPW, a PASS filter with mask `00 FF 00` and pattern `00 6B 00` (the functional reply target); WRITE `68 6A F1` + `01 00`, then `01 0C` (the cached header); DISCONNECT, CLOSE | every call NOERROR; each request answered by one message `48 6B 10 41 00 ..` and `48 6B 10 41 0C ..`. No reply is a FAIL with the rig |
| restore | `j2534_server` as found (a planned restart), the simulator's settings back (one restart) | the settings equal; the simulator on its buses again |
| the chip | the snapshot again, `eeprom_guard` | no cell changed, `protocol_saves` 0, the stored protocol as found |
| the unit | the restart history | the boot count up by the planned restarts only, no unexpected reset |

## Records

- **2026-11-11, the first run against a VPW node, and what it found** (bench DUT 44bd8d73ca79, chip MIC3624 V2.3.24, store 6, ECU sim v2). Until that day the bench's simulator was CAN-only and this bench ended in SKIP every time: the reply path had never run. With the simulator on VPW alone the app path answered (`ATTP2`, `0100` reads `48 6B 10 41 00 18 3F 80 03 0F`, `ATDPN` 2) and the J2534 channel did not: CONNECT ok, the log `J1850 VPW channel up`, WRITE accepted, no reply, 2 of 2 runs, and `ATDPN` `6` over `/ws/obd` afterwards. Two faults in the channel's init (the components repo, `j2534_channel_legacy.c`), found with the init's own words sent by hand over `/ws/obd`:
  - **`ATD` stood behind `ATTP2`.** "All to defaults" puts the chip back on its stored protocol: `ATTP2`, `ATD`, `ATDPN` reads `6`, and the request goes out on CAN (`NO DATA`). With `ATD` first and `ATTP2` behind it `ATDPN` reads `2`.
  - **The three ST pass filters let nothing through.** With `ATD` first and `STFAP 00F000,00FF00`, `00FE00`, `006C00` as shipped, `0100` under the header `68 6A F1` still read `NO DATA`; with filters for 6B and F1 added, still `NO DATA`; with `STFCP` alone (no pass filter) it read `486B104100183F80030F`. On CAN, where the chip sat because of the first fault, the same three words answer `?`.
  - This bench's own PASS filter asked for replies to F0 while its request is functional (`68 6A F1`, answered `48 6B 10 ..`): it would have dropped the reply it was waiting for. It passes 6B now.
- Red with the rig on the image before the change (sha256 `d8755bc1`): `J2534 VPW FAIL (2 failed)`, both requests accepted and unanswered. Green with it (sha256 `c8efdc03`): **`J2534 VPW PASS`**, 15 checks, `01 00` answered `48 6B 10 41 00 18 3F 80 03 0F` and `01 0C` answered `48 6B 10 41 0C 0C 80 02` (800 rpm), the chip's 40 cells the same, `protocol_saves` 0, two planned restarts; the same through `.\test.ps1 j2534vpw` (103 s). Regression on that image: `J2534 STD TOOL PASS` 28 checks (the CAN side of the server, the installed DLL driven by udsoncan), the host suite `j2534_server` 28, `RELEASE TEST PASS` 59.

## Not covered

- **Physical addressing.** The simulator answered no `6C 10 F1` request on the app path, with or without filters, so only the functional exchange is proven. The channel's receive is the chip's auto receive (`ATAR`): replies to the address in the request header's source byte, and functional replies.
- **The last byte of a delivered message is the J1850 CRC** as the chip prints it with headers on (`0F` and `02` above). Whether a J2534 application expects it stripped is open; nothing was changed there.
- Unsolicited traffic between requests (the channel is request and response only), GM 4x programming speed.

## Traps

- The simulator boots with every bus live, and a chip left to search would settle on CAN: the rig puts it on VPW alone and restores its settings in the `finally`. A run killed in between leaves it on one bus (the bench's restore, or the simulator's own page, puts it back).
- `j2534_server` is off in the bench unit's settings as found; the bench switches it on and off around the session, which is where its two planned restarts come from.
- With `exclusive` set (the default) AutoPID is paused while a tester is attached; the log reads `autopid: paused: a diagnostic tool holds the bus`.
