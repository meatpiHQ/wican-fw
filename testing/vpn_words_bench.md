# The HA webhook push carries vpn_manager's own state word

Command: `python tools/testbench/vpn/vpn_words_bench.py [--dut localhost:18085] [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--port 8197] [--only w1,w2] [--logdir DIR]`

Needs: DUT on the bench AP through rpi001; wg-bench on the Pi

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The HA webhook push carries vpn_manager's own state word** (→ `VPN WORDS PASS`, ~10 min, 3 planned restarts plus the push bench's; issue #985, 2026-10-16). ha_webhooks mapped `vpn_state_t` through a four-entry table after `VPN_STATE_PAUSED` joined the enum: `connected` read past the table, `paused` pushed "connecting"; the push takes `vpn_manager_state_str()` now. The bench runs `ha_webhook_sta_ip_bench.py` (which checks `status.vpn_status` against `GET /api/vpn` and the five words since the same day) with the VPN disabled as found, connected through the Pi's LOCAL wg-bench (a device keypair, the DUT as its peer, `connected` within 90 s, `vpn_ip` in the push) and paused (the bench AP marked Pause VPN, no `vpn_ip`), then restores the vpn_manager and wifi_manager objects through the version-1 export so the device's own keys come back. Needs a valid clock on the DUT (the handshake): without NTP through the Pi, set it first.

**RAN 2026-10-16 on `alfa-04-6-g0d92af5-dirty`: `VPN WORDS PASS`, 17 of 17** (connected 27 s after the tunnel's restart with `vpn_ip` in the push, paused 2 s after the mark without it, the push bench green in all three states, 3 planned restarts, 0 E lines, heap floor 44,135 B); a first run the same hour passed every device-side check and failed only its own verdict parsing (two spaces before the sub-bench's detail), fixed.
