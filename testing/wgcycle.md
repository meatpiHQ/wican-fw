# WireGuard torn down while MQTT runs through it, N times

Command: `.\test.ps1 wgcycle` (= `python tools/testbench/vpn/wg_teardown_cycle_bench.py --dut localhost:18083 --dut-ip <ip> --pi rpi001`; by hand: `python tools/testbench/vpn/wg_teardown_cycle_bench.py [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--cycles 10] [--trigger wifi|peer] [--hold-s N] [--logdir DIR]`)

Needs: DUT on the bench AP + rpi001 (`wg-bench`, the mosquitto on 1883)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it is.** The witness of the two beta.5 crash reports of 2026-11-01 (task
`vpn_state`, a double free in `tlsf_free` and an InstructionFetchError in
`tcp_abandon`, `internal/TASK_wg_teardown_race.md`): `esp_wireguard_disconnect()`
cleared the tunnel address from the state task and lwIP aborted and freed every TCP
connection on that address off its own thread. The bench stages WireGuard to the Pi's
`wg-bench` (the DUT's key through `/api/vpn/keygen` + `wg-bench peer`), points the
DUT's MQTT at the broker's TUNNEL address (10.66.0.1, so the broker connection is a
TCP connection on the tunnel address: the one the teardown aborts), and tears the
tunnel down N times. The trigger: `wifi` (default) posts a station trial to a network
that does not exist (`/api/wifi/try`), the station leaves the bench AP for a few
seconds, `network_up()` goes false and the state task tears the tunnel down at once
(the uplink-lost path; about 30 s per cycle); `peer` removes the DUT's peer from
`wg-bench` so the keys expire and "peer silent for 30 s; reconnecting" fires (the
field's other path; 2 to 4 min per cycle). `--hold-s N` (2026-11-09, the `peer`
trigger only) keeps the tunnel up for N seconds first and checks it is still
connected on the same `connects`, so WireGuard has rekeyed (every 120 s) before the
peer goes: the state every tunnel older than two minutes is in. `--hold-s 150` is the
witness of the key that never expired (see the 2026-11-09 records below). Each cycle
checks `connects` on `/api/vpn`
grew by one, MQTT is back through the tunnel and `boot_count` did not move; the
closing checks are the as-found settings (one restart), no unexpected reset, no E line
of `vpn_manager` / `ip4_routes` / `esp_wireguard` / `mqtt_manager`, the state task's
headroom and the internal heap floor. `wg-bench` is stopped in the `finally`.

**The diagnostic build.** With `CONFIG_LWIP_CHECK_THREAD_SAFETY=y` in `sdkconfig`
(not the release configuration: an assert is a crash) lwIP asserts
`Required to run in TCPIP context!` at the first core call off the tcpip thread. On
the code before the fix the DUT crashed 9 s after the boot that enabled the tunnel
(`assert failed: netif_add netif.c:297`, task `vpn_state`, backtrace
`esp_wireguard_netif_create` <- `esp_wireguard_connect` <- `vpn_wg_up` <-
`state_task`), three times, and parked; the console capture is
`test-reports/logs/wg_cycle_diag_repro/console.log`. Run this bench on such a build
after any change to the VPN's lwIP paths: a silent run is the proof. The console
capture (`tools/testbench/lib/serial_capture.py COM254 2000000 420 <log> NO_SUCH_MARKER`,
started before the bench; opening the port resets the DUT) shows the assert and the
backtrace when there is one; `/api/restart/history` holds the crash note otherwise.

**2026-11-02 (the reproduction, the code before the fix, the diagnostic build):
FAIL as expected**, `c1_connected` never (the assert at every connect, the park),
`test-reports/logs/wg_cycle_diag_repro/`.

**2026-11-02 14:01 (the fix, the same diagnostic build): PASS** (10 of 10 cycles,
8 to 11 s each under the `wifi` trigger, `connects` 1 to 11, MQTT back through the
tunnel every time, `boot_count` unchanged, no assert, no E line, `vpn_state` headroom
3712 B, internal heap floor 56,416 B; `test-reports/logs/wg_cycle_diag_fixed/`, the
bench output in `bench.txt`, the console in `console.log` with 30 tunnel up/down lines).
The Tailscale half on the same build: `.\test.ps1 tspublic` first asserted in
`wireguardif_periodic` (`netif_set_link_down` from the `ml_wg_mgr` task, 9 s after the
Tailscale start) and, with the link flags posted, in `wireguardif_process_data_message`
(`ip_input` run from the microlink task for every packet received on the DISCO socket or
the relay): both fixed the same afternoon (the driver posts the link flags and hands
decrypted packets to the netif's own input function, microlink posts its handshake sends),
see `internal/TASK_wg_teardown_race.md` section 7 for the run that followed.

**Traps.** The bench's "as found" is whatever the DUT holds when it starts: a run
that died before its restore leaves `vpn_manager.enabled` true and the MQTT url at
the tunnel address, which the next run then takes for the as-found state; put the real
state back by hand first (the bench DUT: VPN off, `mqtt://10.42.1.1:1883`). Under the
`wifi` trigger the HTTP witness through the Pi is away for the trial's seconds:
unanswered polls are expected, the bench rides them out. A parked DUT (three crashes)
comes back with the next serial flash (the EN reset clears the brake's count).

**2026-11-02 14:25 (the release configuration, the final image of the day): PASS**
(`.\test.ps1 wgcycle`, 143 s: 10 of 10 teardowns under the live MQTT connection through
the tunnel, MQTT back every time, no reboot, no E line, the as-found settings put back with
one restart; `test-reports/logs/` `wg_cycle_*` of that time). The same stage ran green on the
thread-check build first (14:01, above).

**2026-11-09 16:40 (the published beta.8, `--trigger peer --hold-s 150 --cycles 1`, by hand):
FAIL as intended**, the reproduction of the key that never expired on a plain tunnel. The
tunnel connected in 2 s, MQTT through it, held 150 s on the same `connects` (so WireGuard had
rekeyed), then the DUT's peer entry was removed on the Pi: the DUT stayed `connected` for the
whole 300 s the bench waits (`left=False connects 1->1`), the state task's "peer silent" watch
never fired. The driver's `wireguardif_peer_is_up()` counts the previous keypair, which the
first rekey fills and nothing destroyed, so a tunnel older than two minutes read as up for as
long as the device ran: the VPN page said connected with the server gone, and the reconnect
(which also looks the server's name up again) never came. Log
`test-reports/logs/2026-11-09_1640_wgcycle_peer_hold_beta8/`.

**2026-11-09 17:22 (the fix: the driver destroys the previous keypair at REJECT_AFTER_TIME,
ELF `8da296f7c66bb2eb`, the same command): PASS**, `left=True connects 1->2 mqtt=True`, 198 s
from the removal to the tunnel and MQTT back (the keys expire 180 s after the last handshake,
30 s of silence, the reconnect), `boot_count` unchanged, no E line, `vpn_state` headroom
3808 B, internal heap floor 53,424 B. Log
`test-reports/logs/2026-11-09_1722_wgcycle_peer_hold_fix/`.

**2026-11-09 17:27 (`.	est.ps1 wgcycle` on the same fix image, the release configuration):
PASS**, 10 of 10 teardowns under the `wifi` trigger, worst cycle 27 s, 213 s in all, MQTT back
every time, no reboot, no E line, `vpn_state` headroom 3712 B, internal heap floor 53,700 B.
Report `test-reports/2026-11-09_1727_wgcycle.md`.
