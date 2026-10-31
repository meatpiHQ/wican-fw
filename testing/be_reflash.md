# Script-driven ECU flash

Command: `python tools/testbench/obd/run_be.py <base-url> tools/testbench/obd/reflash.be --tx 7E2 --rx 7EA --timeout 90` with `python tools/testbench/actors/pcan_reflash_ecu.py 200 --scenario happy --req 7E2 --resp 7EA` on the PC

Needs: main fw on DUT + PCAN on the bus + SD card

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Script-driven ECU flash** (prints `REFLASH OK` + `SCRIPT RUN PASS`, ~2 s): `reflash.be` streams `/sd/fw/ecu.bin` (640 B on the bench card) as TransferData blocks after session, seed/key, erase and RequestDownload, then TransferExit and the ECU's checkMemory CRC routine. Needs `script_engine.allow_reflash` (reboot-to-apply; restore afterwards) and the SD card. Turn the UDS Exclusive switch on (`POST /api/uds {"exclusive":true}`) so autopid is off the bus. Bench 2026-09-16 over native ISO-TP: REFLASH OK, 5 blocks, `71 01 02 02 00` = CRC verified
