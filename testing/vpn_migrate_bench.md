# The VPN settings migration routes the tunnel address's network

Command: `python tools/testbench/vpn/vpn_migrate_bench.py [--dut localhost:18084] [--dut-ip 10.42.1.194] [--pi rpi001] [--only m1,m2] [--logdir DIR]`

Needs: DUT on the bench AP through rpi001

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The VPN settings migration routes the tunnel address's network** (→ `VPN MIGRATE PASS`, 20 checks, ~5 min, 4 planned restarts; issue #988, 2026-10-16). v1 stored `address` + `allowed_ip` + `allowed_ip_mask` and gave the netif the address with that netmask, so lwIP routed `address & mask`; the first v2 migration wrote `allowed_ip & mask`, a different network whenever Allowed IPs is not the tunnel subnet. The bench feeds `settings_manager_restore()` a version-1 vpn_manager object through `POST /api/backup/inspect` + restore (`on_migrate` runs for an object older than the descriptor): the reporter's pair (address 10.6.0.2, Allowed IPs 192.168.86.0/24) gives `routes` `10.6.0.0/24` (the bug gave `192.168.86.0/24`), the tunnel subnet as Allowed IPs gives the same, the 0.0.0.0 idiom an empty list, the old keys are dropped; then the as-found object (the version-1 export, secrets verbatim, never printed) goes back at its own version.

**RAN 2026-10-16 on `alfa-04-6-g0d92af5-dirty`: `VPN MIGRATE PASS`, 20 of 20, heap floor 52,271 B, 0 E lines.** A unit already migrated on alfa-04 keeps its list (the old keys are gone): a release-note line for those users.
