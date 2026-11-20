# J2534 over the USB cable (the CDC serial transport)

Command: `python tools/testbench/usb/j2534_serial_test.py auto [--rx]` (PC; `auto` finds the port by its USB ids 16D0:1263, or name it: `COM<N>`)

Needs: the unit's USB-C on the PC + on the unit's web page the USB page set to "Connector role" Device and "USB device class" CDC serial, and J2534 enabled (a restart applies them) + pyserial; for `--rx` an ECU that answers 7E0 (the bench simulator)

The index row and the other stages: [../TESTING.md](../TESTING.md). The same protocol over TCP: [j2534std.md](j2534std.md). The page probe for the hint in the web UI: [probe_j2534_usb.md](probe_j2534_usb.md).

## What it covers

The J2534 server's wire protocol over the virtual COM port the WiCAN PRO is to a PC in the Device role with the CDC serial class. No DLL is involved: this is the transport (`usb_cdc_device`, started when the PC asserts DTR) and the server.

| Check | What |
|---|---|
| the port | with `auto`: a port with the USB ids 16D0:1263 is there. When there is none the test ends at once and says which settings make one: as shipped the connector is in the Host role, and the PC then shows only the two COM ports of the board's USB bridge (the console and the OBD chip), which do not speak J2534 |
| HELLO, OPEN | answered NOERROR over serial, wire version 1, a device id |
| CONNECT ISO15765 | with the ids 7E0 / 7E8 on CONNECT, a channel id |
| `10 02` written | ACK NOERROR |
| `34 ..` written | a RequestDownload is forwarded, ACK NOERROR. Until 2026-10-27 a content gate refused it (`ERR_NOT_SUPPORTED`) and this test expected that; it had stayed that way and printed FAIL on a healthy link until 2026-11-20 |
| `--rx` | `10 02` answered `50 02 ..` by the ECU, read back over the cable |

## What it does not cover

- The Windows DLL over the cable (its serial transport, the port search): `internal/components_pr5_pr6/tools/dll_open_check.py --talk` under the 32-bit Python did that by hand on the day of the record; the DLL's legs of the J2534 stages run over TCP.
- Pointing a client at one of the CH342 ports (Host role) by hand: not run on purpose. Those carry the console and the OBD chip, and an open with DTR and RTS can reset the unit.
- Speed and loss over the cable.

## Records

**2026-11-20 22:48, the build with the web page's hint (image `268e0ec3c9709523`, put on by upload; the bench unit 14c19f457fd9, its USB-C on the PC).** As shipped (Host role, J2534 off): `python j2534_serial_test.py auto` ends with `J2534 SERIAL TEST FAIL: no WiCAN PRO serial port (USB 16D0:1263) on this PC. On the unit's web page: USB page, Connector role = Device and USB device class = CDC serial; J2534 enabled; ...`, and the rebuilt driver DLL (1.10, not released) with `WICAN_J2534_COM=auto` returns `PassThruOpen -> 0x08` with the text `no WiCAN COM port found (USB 16D0:1263): plug the WiCAN into this PC and set, on its USB page, Connector role = Device and USB device class = CDC serial`. With the two settings and J2534 on (one planned restart): the port is COM20, **`J2534 SERIAL TEST PASS`**, six checks with `--rx` (`10 02 -> 50 02 00 32 01 F4`), and the DLL over the cable opens, reads 13,898 mV, arms 7E8 / 7E0 and gets `10 03` answered. The settings back as found (one more planned restart), 0 unexpected resets, 0 E lines.

## Traps

- The settings are reboot-to-apply and the port only exists in the Device role: run the test after the restart, and put the role back afterwards if the unit's USB-C is to serve a dongle again.
- In the Device role the board's two CH342 ports are gone from the PC (the connector's data lines go to the ESP32 instead), so a console on them is too. The bench's console is on the CH344, which stays.
- Opening the port asserts DTR and that starts the session; a second program on the port, or the J2534 server serving a TCP client, makes the first request time out.
