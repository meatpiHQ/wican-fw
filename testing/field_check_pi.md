# THE FIELD CHECK: the release checklist's last step from the bench

Command: `python3 -u field_check_pi.py <from> <to> [dut-ip] [channel]` on the Pi (`tools/testbench/release/field_check_pi.py`, scp'd to the Pi)

Needs: rpi001 (python3), the DUT on the P4, the console port for the flash, the lab gateway's internet

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**THE FIELD CHECK: the release checklist's last step from the bench** (prints `VERDICT: FIELD CHECK PASS`, 17 checks, about 3 min; 2026-10-27, first run on 6.0.0-beta.5). A DUT running the PREVIOUS published release takes the new one from the LIVE list the way a user's device does: the channel switched (a settings apply = a planned restart), `POST /api/fw_update/check` = the manifest fetched from raw.githubusercontent.com, `targets.self.latest` the new version and installable, the notes the way the page gets them (since 2026-11-02 the list names `notes_url` and the browser fetches it; a previous firmware older than that proxied the file through `/api/fw_update/notes` with a 16 KB cap, so the step only checks the path the running firmware has and logs the old route's answer otherwise), `POST /api/fw_update/install` with the job watched to `rebooting`, the new image's boot (`/api/info` fw_version and build), a planned restart so the pre-restart hook confirms it, `last_result` verified from the old to the new version and `rollback.ota_state` valid, the channel back to stable, no fault. Around it by hand (the bench is air-gapped): flash the previous release's published flash set on the DUT over the console port (the zip from the GitHub release, its sha256 against the manifest, `esptool write-flash @flash_args.txt`; Ali removed the files of beta.1 to beta.5 on 2026-11-02 so users stop installing them, and they must not come back: then a local build of that version goes into ota_0 with `build/ota_data_initial.bin` at 0xd000, `python -m esptool ... write_flash 0xd000 ota_data_initial.bin 0x10000 <app.bin>`, the PC python's esptool 4.12 spelling), on the Pi `ip route` first (a DHCP default route via eth0 makes the next step unnecessary) else `sudo -n ip route add default via 192.168.8.1 dev eth0 metric 500` (NetworkManager's shared profile masquerades 10.42.1.0/24 already; the DUT's gateway and resolver are the Pi), run, then `ip route del` the same route.

**2026-10-27 20:00, beta.4 to beta.5: PASS** (the manifest fetched on the first check, beta.5 offered with its summary, the notes 16,029 bytes rendered, 3,881,136 B downloaded from the GitHub release in 38 s, booted v6.0.0p-beta.5 on ota_1, verified at the planned restart under the rollback bootloader v2; the first run's script misread two fields, fixed in the tool)

**2026-11-02 23:04, beta.5 to beta.7: PASS** (the DUT on a local beta.5-era image, the issue-1023 build in
ota_0, since the published beta.5 files are gone on purpose; the Pi already had a DHCP default route via
eth0; the manifest fetched on the first check, beta.7 offered and installable, the beta.5 firmware's own
notes route answered 502 for the 20 KB notes (logged, not a failure: that firmware predates the page-fetched
notes), 4,049,472 B downloaded in about 42 s, booted v6.0.0p-beta.7 on ota_1, verified at the planned
restart under the rollback bootloader v2, channel back to stable, no fault; 0 failed. The run before it,
23:00, had the same install path green and three failures of the notes step as first written, which
assumed `notes_url` on the previous firmware: fixed in the tool)
