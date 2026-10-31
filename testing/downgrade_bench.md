# A unit on v6 with v6's own partition table taken back to the legacy firmware through v6's OTA page, and forward again through the legacy...

Command: `python tools/testbench/system/downgrade_bench.py --image <v6.bin> [--build build] [--legacy-dir <the v4.51p zip, extracted>] [--resume-up --ssid WiCAN_<id>]`

Needs: DUT console + PSU + rpi001 (wtest1 / wint0 on the DUT's AP)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**A unit on v6 with v6's own partition table taken back to the legacy firmware through v6's OTA page, and forward again through the legacy page** (prints `DOWNGRADE PASS`, 13 checks, ~10 min; 2026-10-10, Ali: "what happens if the user decides to go back to the old firmware?", the layout a migrated unit will have once v6 rewrites the table of a unit updated from v4.5x). Erases the DUT, writes the v6 set (the build's bootloader, table and ota data, the image at 0x10000), cold boot with the console (0 E lines, no fault, the AP up), rpi001 sets a marker through v6's API (`ap_password` `ota-bench-2026` via PUT + submit, the device reboots, the AP takes the new password), POSTs the legacy app to `/api/ota/upload` (`firmware=`, `{"ok":true,...,"reboot":true}`, 28 s), PSU cold boot: `Running firmware version: v4.51p`, `LittleFS filesystem mounted successfully`, `Partition size: total: 6029312` (v6's 5888 KB `storage`: the legacy mounts by the name and takes the size from the superblock, its config.json defaults land beside v6's /data files), the legacy web root on the factory AP, then the v6 image through the legacy `/upload/ota.bin` (303, 28 s), PSU cold boot: v6 from ota_0, 0 E lines, 0 persist failures, no fault, and the AP still takes the marker password (v6's `settings` partition untouched by the legacy firmware). `--resume-up` starts at the legacy web check on a DUT already taken down.

**2026-10-10: PASS** (`test-reports/logs/downgrade_20261010_0301` down to the legacy under v6's table, `_0308` the way back after two bench bugs of mine: the legacy web root is not UTF-8 clean, and an import inside the skipped block).

**With the fix (`partition_migrate`) in the image: PASS 13/13 in one run** (`downgrade_20261010_0409`, 8 min: the legacy lands in ota_1, mounts v6's 5888 KB storage, v6 back from ota_0 with the marker password and 0 E lines). Leaves the DUT on v6 with the AP password `ota-bench-2026`, no STA
