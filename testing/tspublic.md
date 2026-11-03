# Tailscale against the public rig

Command: `.\test.ps1 tspublic` (= `bash tools/testbench/vpn/ts_public_soak.sh run 3` with `DUT_IP` set to the DUT's bench-AP address, Git Bash by path; by hand: `DUT_IP=10.42.1.194 bash tools/testbench/vpn/ts_public_soak.sh up | status | down | run [minutes]`)

Needs: betty (the root ssh alias; headscale 0.23.0 and tailscale 1.78.1 under `/opt/tsbench`, `provision` once) + rpi001 (the script talks to the DUT through the Pi, no tunnel) + the DUT on the bench AP with an internet path (the Pi's NAT or the dongle's LTE)

Index row and the other stages: [../TESTING.md](../TESTING.md). The row
was lost in the 2026-10-31 split of TESTING.md and restored on 2026-11-01
with this page; the 2026-10-31 records below are from the hand-over
documents of that day (`internal/TASK_tailscale_1024.md`,
`internal/TASK_ble_tailscale_margin.md`).

**What it covers.** `up`: headscale from a fresh state on betty (the public
Tailscale DERP map), a disposable preauth key (root-only file, delivered to
the DUT over curl's stdin, never printed), the DUT joined as `wican-pub`
through `vpn_manager` type `tailscale` (one submit, one restart), a real
tailscale peer `betty-peer` on betty (its WireGuard port pinned to 41641),
then the legs: registered, tailnet IP, **the relay-only pass (2026-11-03)**:
before the peer joins, betty drops all UDP to/from the DUT's public IPv4
address (`iptables`, in and out), so no direct path can exist and the
peer never contacts the DUT; the DERP relay up (`/api/vpn` `derp`, issue
1024); **the subnet router legs (2026-11-01)**: betty advertises
`10.99.0.0/24` (an alias on its loopback) and headscale enables the route,
the DUT must list it on `/api/vpn` `routes` within 2 min of the map update
and a destinations test (`POST /api/destinations/test`, the `tsroute` row
the script stages, with scheduled sending disabled, to
`http://10.99.0.1:8199/route`) must arrive at the
receiver on betty (`dd_receiver.py --no-tls`) from the DUT's tailnet
address: in the relay-only pass that request is the first application
exchange between the two, initiated by the DUT, carried by the relay (the peer's
own region, Sydney for betty, the DUT's home being Dallas), and the pass
asserts that the peer reports a relay, not a false direct endpoint.
Five more requests 30 s apart exercise the session beyond WireGuard's
120 s rekey boundary, all six must answer 200 and arrive exactly once,
and the OUTPUT drop counter must be above zero. The drops come off,
then the direct-path legs: disco ping, ICMP and HTTP
through the tunnel, the DUT's peer surface, the subnet request once more
over the direct path; **the task stacks (2026-11-01)**: `vpn_state` and
`ml_wg_mgr` read `stack_ext` true on `/api/status/tasks`; then the internal
RAM flat over 60 s (`up`) or over the soak (`run`, 30 s samples of the
state, the peers, the relay, the free internal RAM), and zero error logs
and faults. `down` restores both original settings documents, submits a
planned restart and compares the returned documents byte-for-byte with
the originals. Snapshots under `state/ts-public-<ip>` are gitignored and
removed only after verification; a failed restore retains them for another
`down`. The peer, headscale, receiver, alias and disposable keys are removed
from Betty. Processes are stopped by saved PID, and only the bench's tagged
firewall rules are deleted. `run` tears down from an EXIT trap, and cleanup
failure fails the stage even if the traffic checks passed.

**Records.**

- **2026-11-03 15:57: TS PUBLIC PASS**, 559.9 s, final non-diagnostic ELF
  `8b3f8068ef52c85b`, image 4,052,432 B. Same-transport DISCO replies fixed
  the false `direct 127.3.3.40:5` endpoint reproduced at 15:28 and 15:36.
  First relay request: HTTP 200 in 1234 ms; five warm requests:
  532/396/319/744/328 ms, all six received exactly once from the DUT
  (first source verified as 100.64.0.1), with 141 UDP packets dropped.
  Direct recovery: DISCO 91 ms, ICMP 8/8, HTTP 200, subnet HTTP 200 in
  98 ms. `vpn_state`/`ml_wg_mgr` PSRAM headroom 3816/4012 B. Internal RAM
  88,519 to 88,519 B, zero errors/faults. Original VPN and destination
  settings restored byte-for-byte, Betty processes/keys/rules removed.
  The running ELF was checked against the local build after restoration.
  Report: `test-reports/2026-11-03_1557_tspublic.md`.
- 2026-11-03 15:45: all relay/direct traffic checks passed after the DISCO
  fix, but the new zero-error gate found the fixture's scheduled boot-time
  send before the receiver existed (three E lines). The row is disabled
  now; explicit Test requests still work. This run is **FAIL**, not a
  waived error budget. Report: `test-reports/2026-11-03_1545_tspublic.md`.
- 2026-11-03 morning (historical failure before the DISCO fix; the
  relay-only pass, written for the field report of an MQTT
  broker behind a home subnet router unreachable from a phone hotspot;
  `internal/TASK_tailscale_relay.md`): on the published beta.7 the
  relay-only request fails (the DUT never starts a session for its own
  traffic, and relays only through its home region 9 while betty is homed in
  5: `DERP PeerGone` after every packet). With the session-on-demand, the
  per-region relay pool and the `ip4_routes` link-independent routing, the
  session comes up over the relay (`session wanted ... handshake through the
  relay`, up in 539 ms, betty's wireguard-go accepts the keepalive) and a
  request on a confirmed session passes over the pure relay (12:00: 200 in
  361 ms, the receiver's record from 100.64.0.1, direct UDP blocked by the
  DUT's public IP, 11 packets dropped); but on a session the DUT initiated the
  data segments after the TCP handshake are not acknowledged by betty, and a
  warm session later failed five requests in a row at the SYN while betty
  re-handshaked ("stopped hearing back after 15 seconds"). **`TS PUBLIC FAIL`
  on every run of the day (the relay-only request and, after the unblock,
  the disco ping, ICMP and HTTP legs); the relay session's stability with the
  DUT as initiator was the open item, resolved by the 15:57 run above.** The three runs:
  `test-reports/logs/2026-11-03_1052_tspublic` (the port-41641 drop, direct
  leaked through tailscaled's second socket), `_1114_tspublic` (the drop by
  the DUT's public IP, no session: the routing deadlock),
  `_1132_tspublic` (the deadlock fixed, session up, data not acknowledged).
- 2026-10-31 13:40 (the issue 1024 fix: the relay's TLS 1.3 tolerance, the
  leak, lwip_guard, the 30 KB floor): `TS PUBLIC PASS` 302 s on the image
  `96b9c640f76cb3d3`, the relay connected, RAM flat 31123 to 31327 B over the
  soak.
- 2026-10-31 18:14 (the per-step RAM lines): PASS 302 s; the map of
  Tailscale's internal RAM (`vpn_manager/README.md` Footprint): 22.3 KB
  steady, 25.7 KB at the lowest point, three task stacks 17.5 KB of it.
- 2026-10-31 evening (the BLE controller's code in flash, Bluetooth on the
  Lean profile beside the tunnel): PASS 303 s, 18.6 KB steady, 15.2 KB lowest,
  the first run of both together.
- 2026-10-31 23:40, the reproduction of the Discord question ("should pushing
  routes via Tailscale work? ... works on my phone, WiCAN says it can't
  connect to the MQTT server") on the committed image `a73e9ef`, the subnet
  router legs run by hand after `up`: the route advertised and enabled on
  headscale, `/api/vpn` without any route, the destination test
  `ESP_ERR_HTTP_CONNECT` after 6 s, the receiver empty. REPRODUCED.
- 2026-10-31 23:50, the new image (microlink reads AllowedIPs) flashed onto
  the joined DUT: `routes ["10.99.0.0/24"]` within 10 s of the boot, the
  destination test 200 in 81 ms, the receiver's record from 100.64.0.1,
  `vpn_state` and `ml_wg_mgr` on PSRAM stacks (3884 / 4192 B headroom),
  0 flash job refusals, 84.0 KB internal free with the tunnel up. The full
  stage on the same image follows below.

**Traps.** An enabled destination sends immediately after boot, before
the receiver or route exists, producing three TLS error lines and violating
the first-request premise. The fixture row is disabled: the Test endpoint
explicitly supports disabled rows and is the only sender during this bench.
Do not edit the running shell script; Bash may resume reading it at an old
byte offset. A failed snapshot before any settings change aborts safely.

`WG_BENCH_DNS` (env, never committed) makes the control URL a
name so the DNS path runs too. The DUT's egress may be the dongle's LTE (the
STUN-mapped address was the carrier's); the first direct path over LTE can
take over 30 s, so the ICMP legs wait for `direct` in `tailscale status`
first. Earlier script versions used `pkill -f`, which could kill their own
SSH shell; the current script uses PID files and verifies process ownership.
The tailscale `set --advertise-routes` warns that IP forwarding is off on
betty: harmless, the subnet's address is betty's own (loopback). headscale
0.23 lists routes with `routes list -o json` and enables one with
`routes enable -r <id>`; a route is `enabled` only after that, and the DUT
learns it from the next map update (a `PeersChanged` entry with the new
`AllowedIPs`).

- 2026-10-31 23:53, `.\test.ps1 tspublic` on the new image (`v6.0.0p-beta.6-6-ga73e9ef-dirty`,
  the follow-ups of `internal/TASK_httpd_psram_followups.md`): **`TS PUBLIC PASS`**, 326 s,
  every leg: the subnet route learned (`"routes":["10.99.0.0/24"]`), the destination test
  200 in 97 ms, the receiver's record from 100.64.0.1, `vpn_state` 3884 / `ml_wg_mgr` 4096 B
  headroom on PSRAM, the relay up, the internal RAM flat over the 3 min soak (83,683 to
  83,531 B: Tailscale steady costs about 10 KB on this image, 22 KB before the stacks
  moved; the per-step lines are in `vpn_manager/README.md` Footprint). Report
  `test-reports/2026-10-31_2353_tspublic.md`, console `test-reports/logs/2026-10-31_2353_tspublic/`.

**2026-11-02 14:16 (the WireGuard teardown race fix, `internal/TASK_wg_teardown_race.md`,
on a `CONFIG_LWIP_CHECK_THREAD_SAFETY=y` build): PASS** (the register, the tunnel IP,
the direct path via the STUN-mapped address, ICMP 8 of 8 and HTTP 200 through the tunnel,
the DERP relay up, the subnet route learned and a request through it from 100.64.0.1,
`vpn_state` 3820 / `ml_wg_mgr` 4192 B headroom on PSRAM, the internal RAM flat over the
3 min soak, 89,379 to 89,227 B). The two runs before it on the same day's builds asserted
inside the microlink task (`netif_set_link_down` in `wireguardif_periodic`, then
`ip4_input` from `wireguardif_process_data_message`: every tunnel packet received on the
DISCO socket or the relay ran lwIP's IP and TCP input on that task) and are the reason the
driver posts its link flags and hands decrypted packets to `netif->input` now, and the
microlink task posts its handshake sends; this run is the proof the Tailscale data path
runs on the tcpip thread. Logs `test-reports/logs/2026-11-02_1409_tspublic/` (the stage output in
`stage_diag_rxfix.txt`); `2026-11-02_1403_tspublic/console_ts_public.log` holds the `ip4_input`
assert of the second run, `2026-11-02_1355_tspublic/` the first run's `netif_set_link_down`.
