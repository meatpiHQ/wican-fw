# BLE bonding + security end-to-end

Command: `.\test.ps1 blesec`

Needs: main fw on DUT + rpi001 + UB500

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**BLE bonding + security end-to-end** (`ble_security_test.py` → `BLE SECURITY PASS`). BLE sits on the internal-RAM cliff with the full stack, so it auto-switches the DUT to **WiFi-off / BLE-on** (~53 KB free), runs three scenarios on the Pi's UB500 dongle: **gate** (a WRONG passkey can't pair, so no characteristic is readable), **pair** (the correct passkey pairs via MITM and the gated Device-Info read works), **persist** (after a DUT reboot the bonded central reconnects with **no** passkey: NVS bond persistence), then restores `apsta` + BLE-off. Reboots the DUT ~3×; leaves it on WiFi. Runs on the PC (USB link + ssh to the Pi)
