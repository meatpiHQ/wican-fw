# The easy-setup surfaces on the DUT

Command: `.\test.ps1 espnleasy` (= `python tools\testbench\usb\espnetlink_easy_bench.py --dut-ip 10.42.1.194 --pi rpi001`; by hand also `--dut localhost:18081`, `--no-away`)

Needs: PC + rpi001 + P4

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The easy-setup surfaces on the DUT** (→ `ESPNL EASY BENCH PASS`, ~3 min, 2026-10-13): e0 the `usb` summary on `/api/status` (kind, device id, SIM, LTE, operator, fix) agreeing with `/api/espnetlink`'s own dongle reading, `gps_hint` present and 0 at home; e1 the `sim_pin` key on GET and in the schema, a no-op PUT + submit accepted with no restart; e2 the served page carries the bar and the header segment, the wizard chunk the three doors (gzip, inflated); e3 `ap off` on the P4 for 100 s, the DUT home again within 180 s after `ap on`, the count back at 0 and the `usb` hint never fired (a dongle with a fix makes the away count a note: the rule itself is host-tested). Needs the P4 daemon (`--no-away` skips e3) and the dongle's LTE up (about four minutes after a dongle restart). 2026-10-13: run 1 every check PASS with the verdict lost to the bench's own closing call (fixed), run 2 FAIL `e3_lte_up_before` two minutes after the dongle's OTA restart, run 3 **`ESPNL EASY BENCH PASS`** (22 checks, home again 12 s after `ap on`, no unexpected reset). 2026-10-18 02:43 `ESPNL EASY BENCH PASS` again on the release pair (dongle `v1.22-61-gcc81591`, 22 checks, home 10 s after `ap on`); 2026-10-22 23:51 `ESPNL EASY BENCH PASS` on the production unit on 1.23.0 with 6.0.0-beta.2 (22 checks, home 34 s after `ap on`); 2026-10-28 02:40 `ESPNL EASY BENCH PASS` on the production unit `14c19f44ff05` on 1.23.1 with a Simbase SIM (Telstra Mobile, the APN relayed by the fixed share pass) against the dev build after beta.5 (22 checks, home 36 s after `ap on`; the WiCAN's SNTP synced during the 100 s away window through the dongle's LTE, its first sync of the boot: the bench LAN had no internet that night)

**2026-11-01 00:09 (the pairing store on the caller's stack, `internal/TASK_httpd_psram_followups.md`):
`ESPNL EASY BENCH PASS`** (177 s), the dongle with its SIM ready and LTE up, the no-op PUT
and submit accepted, the away leg through the P4 daemon. The store itself
(`espnl_pair_store`) runs at every boot with the dongle enumerated from the link task's
PSRAM stack (the USB share pass), so the night's boots exercised its read side; one real
write through `POST /api/espnetlink/pair` from the HTTP server's PSRAM stack was driven by
hand afterwards and the documents put back (the hand-over's section 3.6). Report
`test-reports/2026-11-01_0009_espnleasy.md`.
