# bench_ap: the bench's home WiFi as an instrument

Command: `bash tools/testbench/pi/bench_ap_net.sh up` (on rpi001)

Needs: rpi001 + the P4 board (both USB ports)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**bench_ap: the bench's home WiFi as an instrument** (2026-10-02, `tools/testbench/instruments/bench_ap/`): an ESP32-P4-Function-EV board on the Pi's USB, WPA2 soft-AP on its ESP32-C6 (esp_hosted) bridged raw at layer 2 to a USB CDC-NCM link; the Pi shares 10.42.1.0/24 on `usb0` (DHCP, mDNS, broker unchanged), both mt76/brcm hotspots parked, `dnsmasq-wtest0.leases` shimmed onto the NCM file so every bench's discovery works unchanged, a console daemon logs associations to `/tmp/bench_ap.console.log` and relays `ap`, `ap off | on`, `ap kick <mac>`, `ap channel`, `ap set`. Replaces the stalling mt76 hotspot (it cost 13 of 17 matrix scenarios twice). Pass bar: a client gets a lease from the Pi through the bridge, the DUT answers `/api/sleep`, kick and off/on cycle the station.

**2026-10-02 bring-up: PASS** (wtest1 10.42.1.244, DUT 10.42.1.194 rssi -47, kick back in 10 s, off/on back in 5 s, 0 dropped frames). Known: the C6 slave firmware is old (host 2.12.0 vs 0.0.0, upgrade pending), the board is pre-3.0 P4 silicon (own build config).

**2026-10-02 evening: the first image leaked every frame it forwarded from a station** (`usb_tx_done` freed the wrong argument) and asserted in esp_hosted after about 330 KB of station traffic (nine resets that day, each leaving the DUT in a zombie association until `ap off` / `ap on`; every AutoPID matrix run died in leg 5). Reproduced with 20 downloads of the DUT's log ring, fixed, re-flashed: 6.5 MB through with the heap flat, then three full matrix runs. `ap` prints `heap: free / min / largest` now: look there before blaming the DUT. Build/flash: `instruments/bench_ap/build.ps1` and the README (stop the console daemon, flash from the Pi, `bench_ap_net.sh up`)
