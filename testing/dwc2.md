# DWC2 kill-vs-ISR race hammer

Command: `.\test.ps1 dwc2`

Needs: espnetlink dongle on the USB host port

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**DWC2 kill-vs-ISR race hammer** (`dwc2_hammer_test.py` → `DWC2 HAMMER PASS`, ~10 min): ACM mixed-timeout kill floods + NCM OUT blasts + mid-flight teardown churn vs the 2026-07-17 crash class. 2026-10-23 11:16 `DWC2 HAMMER PASS` on the fixed RNDIS host build v6.0.0p-beta.2-1-g8432a50 with the older unit 894AA5 on 1.23.0 (two rounds, 701 s, no re-enumeration, the link intact, no reboot, the ACM alive, no faults), and 18:47 again on the final build (engine `unreachable` state + the fix) with the production unit (631 s). 2026-10-18 12:40 `DWC2 HAMMER PASS` on the release pair (dongle `v1.22-61-gcc81591`, the wire as RNDIS: two rounds, no re-enumeration, the link intact, no reboot, the console alive, no faults, 586 s); 2026-10-28 02:54 `DWC2 HAMMER PASS` on the production unit `14c19f44ff05` on 1.23.1 (LTE up on a Simbase SIM) against the dev build after beta.5 (two rounds, 704 s, attaches 1 -> 1, seq 4 -> 4, no faults)
