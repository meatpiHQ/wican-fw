# HA-over-VPN bench route

Command: `bash tools/testbench/vpn/wg_ha_route.sh up \| status \| down [--wipe]` (PC)

Needs: betty (root ssh alias) + rpi001 + DUT on the hotspot

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**HA-over-VPN bench route** (→ `WG HA ROUTE UP`, ~3 min, one DUT reboot): betty = WireGuard server 10.9.0.1 (udp 51820), rpi001 (the bench HA, see below) = peer .3, the DUT = peer .2 through its own `vpn_manager` (split mode, AllowedIPs 10.9.0.0/24, no default route). The bench HA's `internal_url` is `http://10.9.0.3:8123`, so the integration registers a webhook URL the device reaches from the bench hotspot AND from LTE, and HA reaches the device at its tunnel address 10.9.0.2 (the integration's `vpn_ip` backup). Same key rules as `wg_public_soak.sh` (server key born on betty, never printed; DUT key never leaves the device). rpi001's own tunnel rides its `Nachos_8042_5G 2` uplink on wtest1. 2026-09-09: UP first try (rpi001↔betty ping, DUT connected, rpi001→DUT ping + HTTP through betty)
