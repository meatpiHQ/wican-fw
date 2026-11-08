# App session capture: a phone app's session on the device's side, on record

Command: `python tools/testbench/ble/app_session_capture.py --session NAME [--witness 192.168.82.1] [--port auto] [--secs N] [--no-stdin]`

Needs: the DUT's console port free (the capture tails it; the open may reset the DUT once), the DUT's USB-C on the PC in the device role for the witness (`app_session_profile.py ble-on` sets it; without it `--witness none`), rpi001 for the fake phone and the WiFi tap

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it is.** The recorder for the app compatibility tests of 2026-11-08 (the
plan: `internal/APP_COMPAT_TEST_PLAN_2026-11-08.md`): a person drives Car Scanner,
RealDash, Leaf Spy or another app on a phone or a tablet against the bench unit
over Bluetooth or the device's own access point, and the device's side of every
session is recorded against one clock: the console (the Bluetooth session lines of
`ble_manager`, the socket server's client lines, the handover lines, every W and E
line), the counters polled once a second (`/api/status` bits and health, `/api/ble`,
`/api/bridges`, `/api/obd_chip`, `/api/sockets`), the person's marks typed on the
tool's terminal, and the log ring at the end. `start <leg>` / `end` cut the
recording into legs (one app on one platform on one transport); each leg gets a
section in `summary.md`: the Bluetooth sessions with their milestones (connect,
connection update, security request, pairing, the data pipe's subscribe, the first
app write, the disconnect with its reason and the session's numbers), the socket
clients, the bytes that flowed on each bridge, the chip's subscriber drops, the
socket server's refusals and reconnects, the health counters, the W and E lines.
Prints `APP SESSION CAPTURE DONE legs=N folder=...`.

**The companions.**

| Tool | Does |
|---|---|
| `tools/testbench/ble/app_session_profile.py` | the device's shape: `snapshot` (the as-found documents into `test-reports/logs/app_sessions/as_found.json`), `ble-on` (Bluetooth on, the USB connector in the device role for the witness, optionally `--conn-profile`, `--autopid`, `--handover`), `realdash` (the CAN controller on and the RealDash framing on the Bluetooth pipe and TCP 35000), `elm` (the factory bridges back), `show`, `restore` (the as-found documents back, with a diff: `APP SESSION PROFILE RESTORE PASS`). `--via rpi001:10.42.1.75` goes through the bench Pi (the only way in while the connector is in the host role), `--dut 192.168.82.1` is the USB link |
| `tools/testbench/ble/app_session_tap.py` | the ELM TCP tap on the Pi's wtest1 on the device's access point for the WiFi legs (`start` / `stop --out DIR` / `status`); the app is pointed at 192.168.0.11:35000; `stop` copies the log back and prints the tap's analysis (`testing/elm_tcp_tap.md`) |
| `tools/testbench/ble/app_session_phone_pi.py` | the fake phone on the Pi's UB500 (through `ble_pi_run.py`): connect, pair, subscribe FFF1, a Car Scanner style init and the hinted dashboard loop for `--secs`, `--cycles` sessions in a row, `--drop-bond` for a fresh pairing; `--mode realdash` counts the RealDash frames. Prints `FAKE PHONE cycle N: ...` and `FAKE PHONE DONE` |
| `tools/testbench/can/dash_player.py` + `tools/testbench/can/fixtures/realdash_wican_bench.xml` | a moving dashboard on the bench bus from the PC's PCAN (RPM sawtooth, speed, coolant, a counter frame) and the RealDash XML that maps it |

**What the firmware logs (the change of 2026-11-08, `ble_manager/README.md` "Session
log lines").** Before it, a session left `client connected` and `client disconnected`
on the console and the build compiles the debug level out. Now: `client connected:
peer <addr> (<type>), interval .. latency .. timeout ..; asking <min>-<max> ms`,
`connection update: interval .. (N ms after the connect)`, `security request sent`,
`pairing success (bonded N)`, `data pipe subscribed (notify, by the central | restored
from the bond, N ms after the connect)`, `first app write N ms after the connect
(secured, M B)`, `client disconnected: <reason> (0x...); .. s, secured .. s, first write .. s, last
write .. s before end, pipe subscribed, app->dev N wr M B, dev->app N ntf M B, drops
sub N tx N`. The
pure words are host-tested (`host_test/main/test_diag.c`, `.\test.ps1 host ble_manager`:
21 tests PASS 2026-11-08 01:16).

**Reading a leg.** A session `never secured` with `no app write`: the central
connected, was asked to pair and left (a stale bond on the phone, a refused passkey, an
app that probes every device). Secured, writes, `0 notifications`: a pipe with nobody
on the other end (no `ble` connection row, the chip down) or an app writing before it
subscribed. A long `last write ... before the end` with `supervision timeout`: the
phone walked away or its app went to the background. Many short sessions with `the peer
ended it` before any write: the app's own retry loop (the RealDash "four attempts"). The
witness's `bit sta_suspended` / `bit ap_suspended` lines show the handover; a WiFi leg
with `bit ble_suspended` or the console's `BLE stopped (AP station joined)` is the
`ap_ble_exclusive` rule: a phone that stays joined to the device's access point keeps
Bluetooth off.

**Traps.** The witness through the station address does not survive a Bluetooth
session (the station is suspended for it): use the USB link. A station on the
device's access point (the tap's wtest1, a phone that auto-joins) stops Bluetooth. An
iPhone re-joins a known access point on its own: forget the network on the phone
before a Bluetooth leg. The console port open may reset the device once: start the
capture, then the first leg. `--secs` with `--no-stdin` is the unattended form (the
dry run).

**Records.**

**2026-11-08 01:25 to 01:50, the dry runs with the fake phone (the Pi's UB500), the
bench unit 44bd8d73ca79 on the diagnostic image (`v6.0.0p-beta.7-12-g107386a-dirty` +
the session lines), Bluetooth on, the ios profile, the USB device role, the capture
unattended (`--secs`, `--no-stdin`):**

- Run 1 (`test-reports/logs/app_sessions/dryrun_fakephone_1`): the Pi held a bond the
  unit no longer had (erased 2026-11-04): sixteen sessions in a row read `client
  connected: peer E8:48:B8:C8:20:00 (public), interval 37.50 ms latency 0 timeout 4000
  ms; asking 20-40 ms`, `pairing failed (status 0x007)`, `client disconnected:
  authentication failure (0x205); 1.0 s, never secured, no app write` (two of them
  `supervision timeout (radio link lost) (0x208)` after 4 to 6 s), the witness's
  `bit sta_suspended` / `ap_suspended` flipping with each. The "pairs and shows
  nothing" signature, on record before any phone touched the unit. `--drop-bond`
  (the Pi forgets the unit) paired on the third attempt with the passkey agent.
- Run 4 (`dryrun_fakephone_4`, the record): two bonded sessions, both `pairing success
  (bonded 1)` at 0.4 s, `data pipe subscribed (notify, by the central)` at 2.2 and
  2.6 s, `first app write` at 2.7 and 3.0 s, `the peer ended it (0x213)` after 21.7 and
  22.7 s; `app->dev 79 wr 469 B, dev->app 80 ntf 1113 B` and `97 wr 577 B / 98 ntf
  1377 B`, `drops sub 0 tx 0`; the fake phone's side 8/8 init answered, 71 and 89
  hinted polls in 15 s (p50 148 to 152 ms, p95 376 to 524 ms: the Pi's bleak/D-Bus
  write spacing on the 37.5 ms interval BlueZ keeps, which sits inside the 20 to 40 ms
  the device asks for, so no `connection update` line), 0 bad answers; the witness:
  every bridge's a2b +2490 B (the chip's fan-out copy), `br_ble_obd` b2a +1046 B, chip
  subscriber drops 0, uart rx_overflows 0, log_errors 0, internal free min 39740 B,
  one W line (`wifi:Haven't to connect to a suitable AP now!` at the handover resume).
  `APP SESSION CAPTURE DONE legs=1`. The leg summary is `summary.md` there.
- The RealDash shape (`app_session_profile.py realdash`, then the dash player at its
  default 82 frames/s, then the fake phone in `--mode realdash` for 15 s): the bridges
  `br_tcp_can` (can <-realdash-> obd0) and `br_ble_can` (can <-realdash-> ble) up; the
  device's session line `21.9 s, secured 0.8 s, no app write, pipe subscribed, app->dev
  0 wr 0 B, dev->app 486 ntf 15680 B, drops sub 0 tx 0`; the fake phone counted 379
  RealDash frames (25.3 frames/s) in 190 notifications of 7580 B: the Pi's notify path
  kept 190 of the 486 the device sent (BlueZ's D-Bus notification drop, known since
  2026-09-21; the phones do not have it). Two open readings for the morning: the
  device sent about 0.9 KB/s where the bus carried 1.6 KB/s with `tx drops 0`, and
  `/api/bridges` showed `codec_errors` equal to `a2b_chunks` on `br_ble_can` while the
  player ran (621 of 621) and `send_errors` 2565 after the session (most of them the
  minutes without a Bluetooth client): where the other half of the frames went is for
  the RealDash legs to look at (`--scale 2` halves the player's load). `elm` put the
  factory bridges back (boot #3).

**2026-11-08 11:27 to 15:21, the app campaign itself (Ali on an Android phone and an
iPad, the recorder on `test-reports/logs/app_sessions/apps_2026-11-08/`, four legs in
`summary.md`, the marks in `marks_in.txt`, the bare-app captures `console_bare_*.log`):**

- Car Scanner, Android, Bluetooth: pairing 15 s (the PIN), then 100 s of `SEARCHING...
  UNABLE TO CONNECT` (the app's `ATSP0` + `0100`, twelve times) before the app pinned
  protocol 6 itself; then 6 polls/s, 0 drops, three close/reopen cycles re-subscribing in
  2 to 3 s on the link Android keeps. ROOT CAUSE found the same morning and written up in
  `internal/OBD_CHIP_ATWS_AUTO_SEARCH_2026-11-08.md`: the chip firmware V2.3.22 loses its
  automatic protocol search after a warm start (`ATWS`, which our bring-up sends and which
  every app `ATZ` is rewritten to) until a power-on; V2.3.24 is immune. The chip was updated
  to V2.3.24 at Ali's word; the app then connected in auto mode in 16 s (a 6.8 s search).
- Car Scanner, iPad, Bluetooth: first connect 57 s (9 s PIN, a 30 s silence of the app
  after its first write, a 7 s search); the first RECONNECT exposed a firmware bug of ours:
  `pairing success` 92 ms before `client connected` (this NimBLE reports a peripheral's
  CONNECT only when its Read Remote Version exchange completes; a bonded iPhone encrypts
  before that), the CONNECT handler cleared the secured flag, the app's writes were refused,
  a security request went out on the encrypted link and iOS asked for the PIN again; Car
  Scanner reported "connection failed". FIXED in `ble_manager_gatt_nimble.c` (the CONNECT
  handler reads the live security state; no security request on an encrypted link; a lazy
  re-sync), flashed at 14:21, proven on the next four iPad sessions: every reconnect
  secured at 0.0 s from the bond, first write 0.8 s, no PIN, 0 drops, 200 B/s on the
  Sensors page (iOS polls twice as fast as the Android phone).
- Leaf Spy Pro, Android, Bluetooth: the app's "Scan 4 BLE" never lists the unit although
  the Pi's dongle sees the advertisement; its classic-serial mode fails on the RFCOMM socket
  (`[BtS] read failed`). The app auto-connects only to the adapters it knows by name
  (LELink, Kiwi 3). Not ours; WiFi untested today.
- RealDash, Android and iPad, Bluetooth (the RealDash shape, the dash player at 82 frames/s):
  on a silent bus RealDash connects, subscribes, writes nothing and drops the link after
  about 7 s, reconnecting every 8 s (the shape of the reporter's "4 to 5 attempts"); with
  frames on the bus both platforms held the link and received the player's full rate
  (about 80 frames/s, 1.6 KB/s, 0 drops), every reconnect secured within 0.3 s.
- RealDash, iPad, WiFi on the unit's access point: the gauge ran; the app closed and
  reopened its TCP session every 57 s on its own (no station leave in between); the
  reporter's one-to-two-second loop did not reproduce.
- Recorder lessons: a redirected stdout that nobody drains blocks `print()` inside a lock
  (fixed: no echo when not at a terminal); a restart on the same folder re-read the old
  console file and sat past the truncated one (fixed: one console file per start); a
  PowerShell `Add-Content -Encoding utf8` puts a byte-order mark before a mark (stripped).
  Restored at 15:24: `APP SESSION PROFILE RESTORE PASS`, the chip on V2.3.24, PP 07 off.

- Traps: a `sed` insert that did not match left the capture's console follower
  without a name it used (`NameError` in the thread, the timeline then carried the
  witness only; the run's `console.log` still had everything): the tool now defines the
  noise filter in one place; the summary line of the first image exceeded the log
  manager's 256-byte line and was cut without its newline (the words got shorter).
