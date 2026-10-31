# USB-Ethernet bench

Command: `.\test.ps1 usbeth`

Needs: main fw on DUT + the adapter on the USB connector + Pi `eth-bench`/`eth-bench-usb` profile

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**USB-Ethernet bench** (`usb_eth_bench.py` → `USB ETH TARGET PASS`): adapter status, HTTP + throughput over the usb-eth netif, reboot re-enumeration The wire may land on either Pi shared profile (`eth0` `eth-bench` 10.42.1.x, or - since 2026-08-26 - a USB-Ethernet adapter on the Pi as `eth1`, profile `eth-bench-usb` 10.42.2.1/24); the script accepts any `10.42.` lease by default, an optional 2nd arg pins the prefix. The DUT address (1st arg) takes a comma-separated candidate list: BOTH Pi hotspots broadcast `WICAN_TEST_AP` (wint0 10.42.0.x, wtest0 10.42.1.x) and the DUT may re-join either after the reboot leg (`10.42.0.194,10.42.1.194` for the 68ee8f5a653d unit; the .62 pin belongs to the other unit). Re-run 2026-08-26 (AX88772B on the WiCAN, cdc_ncm adapter on the Pi, espnetlink build v4.51p_beta): PASS 10/10, HTTP sample 392 KB/s, attaches=1 after reboot; iperf2 over the wire TCP TX 6.45 / RX 7.71 Mbit/s, UDP `-b 20` 7.00 Mbit/s 0% loss 1.68 ms jitter (matches the 2026-07-13 baseline in `iperf_manager/README.md`)
