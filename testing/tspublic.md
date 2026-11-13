# Tailscale against the public rig

Command: `.\test.ps1 tspublic` (= `bash tools/testbench/vpn/ts_public_soak.sh run 3` with `DUT_IP` set to the DUT's bench-AP address, Git Bash by path; by hand: `DUT_IP=10.42.1.194 bash tools/testbench/vpn/ts_public_soak.sh up | status | down | run [minutes]`)

`-SoakMinutes N` changes only the final soak (default 3). Since 2026-11-13,
the DERP task must be present on a PSRAM stack with at least 1024 B headroom
after connection, after the traffic legs and at every soak sample. The boot
count and unexpected-reset count must stay unchanged through the traffic and
soak. A restart that hides the task's minimum is a failure, not a fresh baseline.
The task iterator suspends only one core and can omit a task moved by the other:
missing entries get at most three snapshots, with an explicit warning. A real
low watermark is never retried away or treated as a missing entry.
Run all other DUT-changing stages separately: `stackaudit` can restart the DUT
for its ephemeral DTC-task check.

`.\test.ps1 tspublic stop-dial` (prints `TS DERP STOP PASS`, ~15 min) runs
the disposable fixture's `up`, then `tools/testbench/vpn/ts_derp_stop_bench.py`,
then `down` in finally. MQTT is parked for this DERP-only fault injection and
its full settings document is restored alongside the VPN and destinations.
It is a fault-injection reproduction, not a normal soak.

**What is judged is ownership**, read from the stage's console capture
(`--console`, passed by `test.ps1`; the bench refuses to run without it).
Each of the client's four tasks logs its start and its end, the client logs
`Destroyed`. The rule: the client is never destroyed, and no task of a new
client is started, while a task of the old client has not said it ended.
A stop may miss its 30-second deadline: one relay dial into a dead uplink
blocks in `connect()` for about a minute (seen on the fix at 18:09). That is
counted and reported; the client must then be kept until the task has ended
(`destroy deferred: a task still owns the instance`). How long a stop takes
is reported, never judged. `python tools/testbench/vpn/ts_derp_stop_bench.py
--dut x --judge <console capture>` applies the rule to a capture of an
earlier run, without a device.

Three legs (`--legs abc` by hand picks some):

- **A, the stalled dial.** It resets established TCP/443 connections from
  this DUT on the Pi and drops new dials, then triggers the non-persistent
  Wi-Fi trial to `wican-bench-nowhere` while the relay task retries. A
  private nftables table matches only this DUT's TCP/443 traffic and is
  removed in finally. The VPN must return, recover its relay, and survive a
  40-second watch for delayed callbacks.
- **B, the uplink lost with the relay up** (since 2026-11-13 18:00). The
  same Wi-Fi trial, twice. The relay's own read error usually closes the
  connection a moment before the stop arrives.
- **C, the control connection lost with the relay up.** The DUT's connection
  to the control server (the address and port of `/api/vpn`'s `endpoint`) is
  reset on the Pi and its new dials dropped, twice. The relay stays
  connected, the client reports itself not connected, and after 30 s of that
  the VPN manager stops it: a stop that finds the relay connected, every
  time. The block comes off when the relay task has ended.

After every stop of legs B and C: the VPN and the relay back, a new `DERP I/O
task exiting (stack: N B never used)` line with N of at least 1024 (the relay
task closes its own TLS connection as it ends, on its own stack), and the
free memory, read 8 s behind "relay back" with the new client up (the
largest of three samples). Between the first and the last of those readings
PSRAM free may fall by 16 KiB at most and internal RAM by 4 KiB: like with
like, a client of the same age each time. A reading from before the first
stop is not comparable and is not used. `vpn_state` keeps at least 1024 B of
its stack. At the end: the boot count and the unexpected resets unchanged,
zero error logs, no new fault code.

The device's log ring cannot stand in for the capture: it holds 16 KB and a
Tailscale reconnect rewrites it within seconds (the relay map alone is
longer). The first cut of this bench looked for the missed deadline there
and could not have seen it; it also failed a run whose VPN took longer than
30 s to return, which is what a stop behind a blocked dial legitimately does
on the fix.

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
and the OUTPUT drop counter must be above zero. **The peer's-own-address
legs (2026-11-09, `run` only, so `up` and its callers are unchanged):** a
second node, `betty-peer2` (userspace networking, its own requests through
its SOCKS5 port; headscale's sequential allocation makes it 100.64.0.3,
which the staged `tspeer` destination row needs and the join checks),
joins behind the same UDP drop. After the first subnet request it asks the
DUT for a page, so it OPENS its session itself, the way Home Assistant
talks to a WiCAN; during the five warm requests the DUT also sends five
to that peer's own address (`http://100.64.0.3:8199/peer`), across the
peer's rekey, all 200 and arriving exactly once. Then the lapse: both
tailscaled processes are stopped for 200 s (`TS_LAPSE_S`, past WireGuard's
180 s REJECT_AFTER_TIME) with the DUT sending nothing, the second peer
comes back and stays silent, and the DUT's request to its own address
must answer 200, twice, both arriving exactly once: the DUT has to open
the session itself with no key left and the tunnel's link down. The first
peer returns, the route must be listed and a subnet request must answer.
The drops come off,
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
`down`. The peers, headscale, receiver, alias and disposable keys are removed
from Betty. Processes are stopped by saved PID, and only the bench's tagged
firewall rules are deleted. `run` tears down from an EXIT trap, and cleanup
failure fails the stage even if the traffic checks passed.

**Records.**

- 2026-11-13 15:37: the first crash-report investigation run reported PASS,
  but is **not an uninterrupted crash reproduction**: the concurrent
  `stackaudit` stage caused two settings restarts during it. This exposed the
  missing restart check, now enforced above. The installed image was
  `017f3e07d4fb6ddc` (a development beta.9), not the reporter's
  `c69c8478ea78bd97`. The reporter's exact binary and ELF are archived under
  `internal/release_6.0.0-beta.9` and `internal/elf`; version text alone is not
  sufficient to select the reproduction image. No crash fix was inferred
  from this run.
- 2026-11-13 15:56: isolated development-image baseline
  `017f3e07d4fb6ddc`, 1033.2 s, PASS. DERP headroom 9972 B throughout,
  PSRAM placement confirmed, boot marker `21 0` unchanged, internal free
  85,015 B flat, zero errors/faults, settings restored and fixture removed.
  This still is not the reporter's image; the exact-image run follows.
- 2026-11-13 16:21: exact reported image `c69c8478ea78bd97`, 1059.5 s,
  no restart (`2 0` unchanged), no errors/faults, DERP headroom 9912 B on
  present snapshots. The stage was FAIL: an omitted task was incorrectly
  reported as 0 B by the new check, and the final internal-free sample was
  4472 B below its first sample. This was not a reproduced stack overflow.
- 2026-11-13 16:50: stalled-dial reproduction on that exact image. DERP
  retried after `Stopping...`, failed to exit in 30 seconds, and was
  immediately followed by `Stopped`, another `Stopping...`, `Destroyed`
  and a replacement client. A delayed `lwip_setsockopt_callback` then
  crashed while signaling a corrupted FreeRTOS wait list. The initial
  restore was interrupted; the retained snapshots were restored with `down`
  afterward. Report `2026-11-13_1650_tspublic_stop-dial.md`.
- 2026-11-13 17:14: independent reproduction with MQTT parked. VPN
  returned in 36.5 s after the missed join deadline; the delayed-callback
  watch caught another LoadProhibited, this time from `err_tcp` under
  `tcp_slowtmr`. All three settings documents and the firewall were restored.
  Report `2026-11-13_1714_tspublic_stop-dial.md`. These reproduce the
  live-task ownership bug, not the original report's exact stack-overflow
  signature. Judged afterwards from their console captures (`--judge`),
  both show the rule broken twice: `the client was destroyed while its derp
  task still ran` and `a second derp task was started over a live one`.
- 2026-11-13 17:51, the fix (ownership, the relay closed by its task; ELF
  `bd2b44b0e6be2385`): leg A, VPN back after 13.7 s; `Stopping...`, the
  relay task ended 1.1 s later with 9972 B of its stack never used,
  `Destroyed` 1.2 s after the stop was asked for; nothing in the 40 s
  behind it. The stage FAILED on the bench: its new leg looked for the
  relay task's line in the device's log ring, which a reconnect had
  rewritten. `2026-11-13_1751_tspublic_stop-dial.md`. The bench reads the
  console capture since.
- 2026-11-13 18:00, the same image: leg A 16.4 s, the first uplink loss
  clean. The second caught a relay dial 0.7 s old: the dial stood in
  `connect()` for 60.3 s (`TCP connect failed: 113`), two stop deadlines
  were missed, `destroy deferred: a task still owns the instance` and
  `tailscale shutdown pending: ESP_ERR_TIMEOUT; retaining the client`
  twice, `Destroyed` 61.7 s after the stop was asked for and under 100 ms
  after the relay task had ended, the next client connected. No crash, no
  E line, the rule kept (`--judge`: 2 clients destroyed, 2 deadlines
  missed, 0 violations). The stage FAILED on the bench, which still took a
  missed deadline for the bug: that is when the judgement moved to
  ownership. `2026-11-13_1800_tspublic_stop-dial.md`.
- 2026-11-13 18:13, a diagnostic image, the fix WITHOUT the relay's close
  (ELF `398666f11f3f8787`), to see what the close is worth: ownership kept
  (5 clients destroyed, 2 deadlines missed), the client stopped 31 and 35 s
  after its control connection went, and behind the four stops of legs B
  and C 279,564 B of PSRAM and 4,380 B of internal RAM gone. Expected FAIL.
  It also showed `E ml_coord: TCP connect failed: 113`, once per attempt at
  the blocked control server: W since. `2026-11-13_1813_tspublic_stop-dial.md`.
- 2026-11-13 18:30, with the close and the W line (ELF `a5a4e94db5cd0e1e`):
  ownership kept, zero E lines, internal RAM 92,339 -> 92,227 B, and PSRAM
  still 70,124 B down over the four stops. FAIL, and a second leak: read
  behind every stop by hand (18:51, `--legs bc`), PSRAM free went 2,796,708,
  2,779,324, 2,761,716, 2,744,024 B, about 17.5 KB per stop whatever its
  reason. The client's WireGuard device was never freed (the components
  repo, `microlink/PROVENANCE.md` 24). `2026-11-13_1830_tspublic_stop-dial.md`.
- 2026-11-13 19:01, by hand on the image with the device freed (ELF
  `6da5ee5883b2fde8`), the same four stops: 2,814,552, 2,814,508,
  2,814,144, 2,814,148 B of PSRAM free, internal 93,011, 93,023, 92,791,
  92,715 B; both uplink losses caught a dial in flight this time (69 s
  each, four deadlines missed, the client kept each time); `vpn_state`
  3736 B of stack never used; `TS DERP STOP PASS`.
- **2026-11-13 19:07: TS DERP STOP PASS**, 814.9 s, the stage whole on that
  image (ELF `6da5ee5883b2fde8`, image 4,110,128 B).
  Leg A: VPN back after 13.7 s, the relay task ended 2.0 s after
  `Stopping...` with 9916 B never used, nothing in the 40 s behind it.
  Leg B: 17 s, then 69 s with a dial in flight (two deadlines missed, the
  client kept). Leg C: the client stopped 32 and 33 s after its control
  connection went. PSRAM free behind the four stops 2,814,176, 2,813,928,
  2,813,768, 2,813,772 B, internal 92,339, 92,355, 92,279, 92,203 B; the
  relay task 9,888 to 10,020 B never used, `vpn_state` 3868 B. Ownership
  kept over 5 destroyed clients, zero E lines, no restart, no fault, the
  three settings documents restored. `2026-11-13_1907_tspublic_stop-dial.md`.
- **2026-11-13 19:21: TS PUBLIC PASS**, 888.7 s, the normal stage on the
  same image (ELF `6da5ee5883b2fde8`): 56 checks. First relay subnet
  request 200 in 810 ms, six arrived exactly once, the five requests to the
  second peer's own address exactly once, the legs after both peers were
  away, the direct path; `ml_derp_tx` 10,084 B of stack never used at the
  soak's samples; internal RAM flat over the soak at 85,019 B; boot marker
  `5 0` unchanged; zero E lines, zero faults; both settings documents
  restored. `2026-11-13_1921_tspublic.md`.
- **2026-11-13 19:51: TS DERP STOP PASS**, 766.7 s, with the dial that a stop
  cuts short (the components repo, `microlink/PROVENANCE.md` 25; ELF
  `0a7885455e4ba47c`, image 4,110,848 B). The reproduction it answers: in
  the four runs before it, six of nine uplink losses caught a relay dial in
  flight and brought the VPN back after 66 to 70 s with two deadlines
  missed. Now: both uplink losses back after 17 and 14 s, `relay dial cut
  short: the client is stopping` 12 and 29 ms after `Stopping...`,
  `Destroyed` 0.5 and 0.3 s after it; both control losses `control dial cut
  short` 29 and 87 ms after it, `Destroyed` 0.2 s after it (8.6 s before);
  **stop deadlines missed: 0**. A control server that drops the SYN is
  given up after 10 s (`TCP connect failed: 116`, W) instead of 18. PSRAM
  free behind the four stops 2,814,180, 2,813,872, 2,814,084, 2,814,164 B,
  internal 92,391, 92,371, 92,247, 92,139 B; ownership kept over 5
  destroyed clients; zero E lines. `2026-11-13_1951_tspublic_stop-dial.md`.
- **2026-11-13 20:05: TS PUBLIC PASS**, 873.7 s, the normal stage on that
  image (every dial through `ml_dial()`): 56 checks, internal RAM flat over
  the soak at 85,023 B, boot marker `4 0` unchanged, zero E lines, zero
  faults, both settings documents restored. `2026-11-13_2005_tspublic.md`.
- **2026-11-09 17:06: TS PUBLIC PASS**, 834.1 s, the fix for a peer's own
  address and a lapsed session (ELF `8da296f7c66bb2eb`, image 4,075,264 B,
  448 B more than beta.8). `ip4_routes: 100.64.0.0/10 via wg0` 7.2 s after
  the start, before any peer. First relay subnet request 200 in 1080 ms
  (`session ... up after 291 ms`), five warm 282/271/270/484/498 ms, six
  arrived exactly once. The second peer opened its session (a page, 200);
  the DUT's five requests to its own address 316/273/543/590/319 ms, five
  arrived exactly once. Both peers away 200 s: 24 handshake tries to the
  first peer (the DUT had opened that session, one every 5 s once its key
  had expired), 5 to the second (the driver's own rekey window) and then
  none. The second peer back and silent: the request to its own address
  **200 in 1023 ms** (`session wanted with betty-peer2 (lwIP traffic):
  handshake through the relay`, `up after 566 ms`), the next 308 ms, both
  arrived exactly once; the subnet request after the first peer's return
  491 ms. A raw-socket watch on the Pi's bench interface over the whole run
  (`tools/testbench/vpn/ts_leak_sniff.py`, by hand): **0** packets from the DUT to
  100.64.0.0/10 in clear text. 260 UDP packets dropped, the peer on a
  relay. Direct recovery: DISCO 79 ms, ICMP 8/8, HTTP 200, subnet 83 ms.
  `vpn_state`/`ml_wg_mgr` headroom 3864/4012 B, internal RAM 86,523 to
  86,523 B, zero errors/faults, settings restored byte-for-byte, betty
  clean. Report `test-reports/2026-11-09_1706_tspublic.md`, console
  `test-reports/logs/2026-11-09_1706_tspublic/`, the watch
  `test-reports/logs/2026-11-09_1707_tspublic_fix/leak_sniff.txt`.
- 2026-11-09 16:49: **TS PUBLIC FAIL as intended**, a diagnostic build with
  the key ageing in and the tunnel's own network left out of `ip4_routes`
  (ELF `0e28cee2792e1ede`): everything green up to the lapse, then both
  requests to the second peer's own address `ESP_ERR_HTTP_CONNECT` after
  6 s, none arrived, no `session wanted` line, and the watch on the Pi
  caught the reason: **10 TCP SYNs `10.42.1.75 -> 100.64.0.3:8199` in clear
  text on the bench AP** (five per request). The leg guards the route by
  itself. Report `test-reports/2026-11-09_1649_tspublic.md`, the watch
  `test-reports/logs/2026-11-09_1650_tspublic_mutation/leak_sniff.txt`.
- 2026-11-09 16:23: **TS PUBLIC FAIL as intended on the published beta.8**
  (ELF `493e4b0ef3d8c8f4`), the reproduction the new legs were written for:
  the second peer opened its session, five DUT requests to its own address
  answered (326 to 590 ms) across the rekey; after 200 s away both requests
  `ESP_ERR_HTTP_CONNECT` after 6 s, none arrived, no `session wanted` and no
  `WG INIT` for that peer (the driver had tried five times in its own rekey
  window, 155 to 180 s after the last handshake, and stopped: the stale
  previous keypair read as a session), while
  the subnet request to the first peer, whose session the DUT had opened,
  answered in 310 ms. The three E lines of each failed request also fail
  the zero-error check. Report `test-reports/2026-11-09_1623_tspublic.md`.
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
The peer's-own-address legs (2026-11-09): the `tspeer` row is staged with
100.64.0.3 before the second peer exists (rows apply at the DUT's restart),
so the join order on a fresh headscale is part of the bench, and a
different address fails its own check; the second peer's SOCKS5 port is
127.0.0.1:1055 on betty; a failed request to the peer leaves the HTTP
client's E lines, so the zero-error check fails with it; `relay-only path`
prints every node (the user column matches the grep). The lapse must stay
inside the relay-only pass: with direct UDP open the DUT's own DISCO probe
of the returned peer starts a one-shot handshake and heals the session
without any lwIP traffic, which hides the defect. To see where a failing
request's packets went, copy `tools/testbench/vpn/ts_leak_sniff.py` to the
Pi and run `sudo python3 ts_leak_sniff.py <seconds> <dut-ip> usb0` beside
the stage (the Pi has no tcpdump): it prints every packet from the DUT to
100.64.0.0/10 that shows in clear text on the bench AP's interface, and
their count at the end.

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
