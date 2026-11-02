# BLE bonding + security end-to-end

Command: `.\test.ps1 blesec`

Needs: main fw on DUT + rpi001 + UB500

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**BLE bonding + security end-to-end** (`ble_security_test.py` → `BLE SECURITY PASS`). BLE sits on the internal-RAM cliff with the full stack, so it auto-switches the DUT to **WiFi-off / BLE-on** (~53 KB free), runs three scenarios on the Pi's UB500 dongle: **gate** (a WRONG passkey can't pair, so no characteristic is readable), **pair** (the correct passkey pairs via MITM and the gated Device-Info read works), **persist** (after a DUT reboot the bonded central reconnects with **no** passkey: NVS bond persistence), then restores the DUT. Reboots the DUT ~3×; leaves it on WiFi. Runs on the PC (USB link + ssh to the Pi)

**Since 2026-11-01 (the NimBLE host task on a PSRAM stack, lever 3 of `internal/TASK_ble_tailscale_margin.md`; hand-over `internal/TASK_ble_host_psram.md`):** the bench also reads the firmware's side of the bond write: the `nimble_host` row of `/api/status/tasks` (`stack_ext` true and at least 1 KB of headroom, before and after the pairing and after the reconnect), `health.flash_jobs` of `/api/status` (the marshalled count grew across the pairing: the bond's NVS writes ran on the flash worker; no refusal), and the log ring carries no E line from the Bluetooth tags (E lines of other tags are printed as a WARN: with WiFi off the bench DUT's other clients complain). `--setup` saves the found `wifi_manager` and `ble_manager` documents to `test-reports/logs/ble_security_found.json` and `--restore` puts exactly those back (the earlier restore set `apsta`, a mode the bench DUT never had); a run that starts on a DUT already in the bench profile keeps the earlier found file. The rig: the DUT's USB-C on the PC in the **device** role (`usb_host_manager.role=device`, `device_class=ncm`: the NCM adapter at 192.168.82.1); in the host role (the CH342 ports) there is no USB link and the bench cannot run.

**2026-11-01 01:25, the first image of lever 3 (the host task on a 4 KB PSRAM stack, the six persist functions wrapped): the DUT crashed at 1 s of every boot with Bluetooth on and the crash-loop brake parked it** (54 mA on the PSU, silent on both links; the stage's `wait_dut` sat 16 min on the dead link, which is why `--setup` now keeps the found file of an earlier run). The console across a reset: `assert failed: spi_flash_disable_interrupts_caches_and_other_cpu cache_utils.c:114 (esp_task_stack_is_sane_cache_disabled())`, task `nimble_host`; the decoded backtrace: `ble_hs_pvcy_set_default_irk` → `ble_store_config_init` → `ble_store_config_conf_init` → `ble_nvs_restore_sec_keys` → `nvs_get_blob`: under `CONFIG_BT_NIMBLE_STATIC_TO_DYNAMIC` the host calls the store's LOAD from its own task at every startup. The load is wrapped as well since (`ble_manager_store_nvs.c`).

**2026-11-01 01:33, the second image (the load wrapped, the stack 4 KB): `BLE SECURITY PASS`, 142 s, 15 checks.** `nimble_host` on PSRAM with 1844 B of headroom before the pairing and **1028 B after it** (the ECDH of LE Secure Connections is the deepest path): the standard's 1 KB line, so the stack grew to 6 KB (PSRAM: nothing internal). The pairing marshalled 9 jobs (34 → 43, 3 worker spawns, worker `stack_hw_min` 1460 B), the reconnect after the reboot 33 (the bond-count re-sequencing deletes and rewrites each record, then the CCCDs), 0 refusals, 0 E lines, the bond persisted. Report `2026-11-01_0133_blesec.md`.

**2026-11-01 01:39, the final image (the stack 6 KB): `BLE SECURITY PASS`, 142 s, 15 checks.** 102.1 KB internal free with WiFi off and Bluetooth on (no dongle), `nimble_host` headroom 3924 B before the pairing, **3076 B after it and after the reconnect**, the pairing 5 marshalled jobs (38 → 43, worker `stack_hw_min` 1624 B), the reconnect 33 (1 → 34, 1772 B), 0 refusals, 0 E lines, the bond persisted, the found documents back (station mode, Bluetooth off, no key differs). Report `2026-11-01_0139_blesec.md`.

**2026-11-02 17:40 (the 4.x parity of Bluetooth, `internal/TASK_ble_address_pairing.md`:
the public address, the one-second pairing request, the CCCD gate; the USB role flipped to
device for the run and back): BLE SECURITY PASS** (137 s): the wrong passkey blocked (no
characteristic readable unpaired), the pair with the static passkey, the bond surviving a
reboot with the reconnect reading a gated characteristic without a prompt, the flash worker
marshalling 1 -> 34 jobs with 0 refusals, `nimble_host` on PSRAM with 2984 B headroom after
the reconnect, 0 E lines.
