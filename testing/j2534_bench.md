# J2534 ISO15765 reflash preamble over native ISO-TP

Command: `python3 tools/testbench/usb/j2534_bench.py <dut_ip> --reflash [--tx 7E2 --rx 7EA]` (ON rpi001) with `python tools/testbench/actors/pcan_reflash_ecu.py 150 --scenario happy [--req 7E2 --resp 7EA]` running on the PC (PCAN_USBBUS2)

Needs: main fw on DUT + rpi001 + PCAN on the bus

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**J2534 ISO15765 reflash preamble over native ISO-TP** (prints `J2534 REFLASH PASS`, ~10 s): CONNECT with ids, `10 02`, seed/key `27 01/02`, the multi-frame `34` -> `74`, the `31 01 FF00` erase routine (every message in the SAE J2534-1 layout since 2026-10-19: `write_uds(..., tx_id=<the pair's request id>)`). `--tx/--rx` + the actor's `--req/--resp` pick the CAN pair: use one the ECU simulator does not answer (it answers 7E0/7E1/7E4.. and cannot be switched off while its settings API is gone) and PAUSE autopid (`POST /api/autopid/group {"name":"default","enabled":false}`) so its polls do not land on the same ECU. The UDS isotp transport releases an ECU 5 s after its last request (one session per rx id), so a UDS probe of the pair right before makes the CONNECT fail; wait. Bench 2026-09-16, public build: PASS on 7E2/7EA
