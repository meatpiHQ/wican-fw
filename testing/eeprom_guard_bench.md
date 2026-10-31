# OBD chip EEPROM guard

Command: `python tools/testbench/obd/eeprom_guard_bench.py <dut_ip> [--port 35000]` (ON rpi001)

Needs: main fw on DUT + rpi001 + the ELM bridge

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**OBD chip EEPROM guard** (prints `EEPROM GUARD PASS`, ~10 s): talks to the MIC like an ELM app over the TCP bridge and proves the driver guard (`obd_chip_guard.h`): `ATSP6` is rewritten to `ATTP6` (chip answers OK, `/api/obd_chip` `eeprom_guard.rewrites` +1), `ATPP 0C SV 23` (UART re-baud) and `STWBR` are refused with the ELM `?` (`.blocked` +1, the chip never saw them), `ATPPS` (summary read) passes, the chip still answers after. Needs the TCP ELM bridge (`obd0`/`br_tcp_obd`, canonical bench). Bench 2026-09-16: PASS
