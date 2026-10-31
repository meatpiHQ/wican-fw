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
tailscale peer `betty-peer` on betty, then the legs: registered, tailnet IP,
disco ping, ICMP and HTTP through the tunnel, the DUT's peer surface, the
DERP relay up (`/api/vpn` `derp`, issue 1024), **the subnet router legs
(2026-11-01)**: betty advertises `10.99.0.0/24` (an alias on its loopback)
and headscale enables the route, the DUT must list it on `/api/vpn`
`routes` within 2 min of the map update and a destinations test
(`POST /api/destinations/test`, the `tsroute` row the script stages to
`http://10.99.0.1:8199/route`) must arrive at the receiver on betty
(`dd_receiver.py --no-tls`) from the DUT's tailnet address; **the task
stacks (2026-11-01)**: `vpn_state` and `ml_wg_mgr` read `stack_ext` true on
`/api/status/tasks`; then the internal RAM flat over 60 s (`up`) or over the
soak (`run`, 30 s samples of the state, the peers, the relay, the free
internal RAM). `down` blanks the key and the destination row on the DUT,
takes the peer, headscale, the state, the keys, the receiver and the alias
off betty. `run` tears down from an EXIT trap.

**Records.**

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

**Traps.** `WG_BENCH_DNS` (env, never committed) makes the control URL a
name so the DNS path runs too. The DUT's egress may be the dongle's LTE (the
STUN-mapped address was the carrier's); the first direct path over LTE can
take over 30 s, so the ICMP legs wait for `direct` in `tailscale status`
first. A `pkill -f` pattern that appears in the same ssh command line kills
the shell itself: the bracket trick (`[h]eadscale`) and separate ssh calls.
The tailscale `set --advertise-routes` warns that IP forwarding is off on
betty: harmless, the subnet's address is betty's own (loopback). headscale
0.23 lists routes with `routes list -o json` and enables one with
`routes enable -r <id>`; a route is `enabled` only after that, and the DUT
learns it from the next map update (a `PeersChanged` entry with the new
`AllowedIPs`).

- 2026-10-31 23:53, `.	est.ps1 tspublic` on the new image (`v6.0.0p-beta.6-6-ga73e9ef-dirty`,
  the follow-ups of `internal/TASK_httpd_psram_followups.md`): **`TS PUBLIC PASS`**, 326 s,
  every leg: the subnet route learned (`"routes":["10.99.0.0/24"]`), the destination test
  200 in 97 ms, the receiver's record from 100.64.0.1, `vpn_state` 3884 / `ml_wg_mgr` 4096 B
  headroom on PSRAM, the relay up, the internal RAM flat over the 3 min soak (83,683 to
  83,531 B: Tailscale steady costs about 10 KB on this image, 22 KB before the stacks
  moved; the per-step lines are in `vpn_manager/README.md` Footprint). Report
  `test-reports/2026-10-31_2353_tspublic.md`, console `test-reports/logs/2026-10-31_2353_tspublic/`.
