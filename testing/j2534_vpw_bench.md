# J2534 J1850 VPW (GM Class 2)

Command: `python tools/testbench/usb/j2534_vpw_bench.py <dut_ip> [--port 6809]` (ON rpi001, or through the ssh tunnel from the PC)

Needs: main fw on DUT + rpi001; a J1850 VPW node for the full PASS

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**J2534 J1850 VPW (GM Class 2)** (prints `J2534 VPW PASS` with a J1850 node answering, `J2534 VPW SKIP (...)` when the connect + VPW init + write reached the OBD chip but nothing answered, `J2534 VPW FAIL` only on a real defect, ~5 s): VPW rides the OBD chip, not the native CAN controller (2026-10-19, modelled on the nightjoker7 4.47p fork, rebuilt on the v6 `obd_chip` claim model and the chip's own `VTFullyRequestCk` USDT). CONNECT runs the VPW init on the chip (ATTP2, ATH1, ATAR, STFAP F0/FE/6C), a J1850 message is `[3-byte header][payload]` whole (no CAN-id prefix), a PASS filter on the tester address F0 arms in software, then `68 6A F1 01 00` (mode 01 PID 00, functional) expects `.. 41 00 ..` back.

**The bench ECU simulator is CAN-only, so on the standing bench this SKIPs after proving the reachable half; the reply check needs a real VPW ECU or a VPW-capable node on the bus (the "new tester").** Needs `j2534_server.enabled` + `allow_lan` and the OBD chip ready (reboot-to-apply; restore after)
