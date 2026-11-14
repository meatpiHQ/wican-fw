# Settings in the reply to a push: taken, left alone, refused

Command: `.\test.ps1 pushreply [-Untrusted]` = `python tools/testbench/ha/push_reply_bench.py --dut 127.0.0.1:18087 --pi rpi001 --pi-ip 10.42.1.1 [--port 8447] [--untrusted --usb-ip 192.168.82.1]` through the runner's tunnel

Needs: the DUT on the bench AP with the Pi as its gateway, rpi001 over ssh with `sudo -n`, `openssl`, `python3` and `avahi-publish` there. The bench makes a CA and a certificate under `/tmp/wican_pushreply`, runs its own receiver on ports 8447 (http) and 8448 (https), gives the Pi's loopback the address 203.0.113.9 and publishes the mDNS name `wican-pushreply.local` for it, and removes all of it at the end. `-Untrusted` also needs the DUT's USB-C cabled to this PC.

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

Home Assistant can only register its webhook settings (the addresses and
the post interval) by calling the device (`POST /api/webhook`). A car that
is asleep at that moment, or on LTE without a VPN, cannot be called,
although the car itself reaches Home Assistant with every push. Reproduced
in the integration's test harness on 2026-11-14: after a start of Home
Assistant with the device out of reach the "device unreachable" repair
issue stayed open while pushes arrived, and a post interval changed in that
time never reached the device.

Since 2026-11-14 Home Assistant may hand the settings over in its reply to
the device's own push: a `200` with `{"webhook": {...}}`, the
registration's body plus a short revision (`rev`). The device stores the
revision with the settings and reports it in every push
(`status.webhook_rev`); Home Assistant repeats its settings until the
device reports the revision it wants. The rules and the wire format:
`ha_webhooks/HTTP_API.md`, "Settings in the reply to a push". The pure
rules have a host suite (`.\test.ps1 host ha_webhooks`, 9 tests;
`.\test.ps1 host http_client_manager` for the address classes). This stage
proves them on the device, with a stand-in for Home Assistant that answers
every push the way the leg asks and notes the revision each push reports
(never the payload).

A reply can change where the device sends its data, so the device takes
settings only where the server is known to be Home Assistant or where a
registration would be accepted: https to a host name (chain and name
verified), or a private address not reached through the station address of
a network marked untrusted. The Pi's extra address 203.0.113.9 (TEST-NET-3,
never routed on the internet) is the bench's "public address": the DUT
reaches it through its gateway, which is the Pi, so a push goes to a public
address by IP and by name without leaving the bench.

The legs:

- **t0** as found: the webhook's settings and registration, the certificate
  sets.
- **t1** the Pi's side (the CA, the certificate naming
  `wican-pushreply.local` only, the receiver, the address, the name); the
  CA uploaded as the set `pushreply`.
- **a1** registered without a revision at `http://<pi>:8447/...`, interval
  5 s: every push carries `status.webhook_rev`, empty (the pushes after the
  first are diffs: the key is in each of them).
- **a2** a `204`: nothing changes.
- **a3** a `200` with a valid `webhook` object (a new revision, interval 4):
  taken, in use, `reply.applied` +1, the next pushes report the new
  revision; the push after it is a diff, not a snapshot.
- **a4** the same reply again: `reply.last` is `same`, nothing counted, the
  flash write counter does not move.
- **a5** a `200` with no body, then with `{}`: as a `204`.
- **a6** not JSON, over 2048 bytes, interval 0, an `ftp://` address:
  `not_json`, `too_long`, `invalid`, `invalid`; settings and revision as
  they were; the pushes counted as delivered.
- **a7** a `503` with a settings body: not read for settings, the push
  counted as failed, as before.
- **r1** the certificate set selected through the settings route (a full
  `PUT /api/settings/ha_webhooks`, the web interface's way) and a restart:
  the settings taken from the reply and their revision are still there.
- **b1** plain http to the public address, registered WITH a revision: the
  revision is in the answer, in `GET /api/webhook` and in the pushes; the
  reply's settings are refused (`untrusted`).
- **b2** https to the private address by IP (the chain through the set, the
  name not compared): taken.
- **b3** https to the public address by IP: refused (`untrusted`).
- **b4** https to the host name, which resolves over mDNS to the public
  address (chain and name verified): taken.
- **c1** a registration with a revision and interval 0: `400 interval`,
  nothing changes.
- **c2** a registration without a revision: the device reports none.
- **c3** pinned on the device (`manual_override`): the pushes carry no
  `webhook_rev`, a reply's settings are refused (`pinned`); unpinned, the
  key is back.
- **c4** the interval changed through the settings route and a restart: the
  pushes report no revision (the fingerprint no longer matches); the reply
  that follows puts Home Assistant's settings back.
- **u0, u1** (`-Untrusted`) the DUT's USB role switched to a network device
  and proven to answer at 192.168.82.1 BEFORE anything is closed; then the
  station's network marked untrusted: the API answers 403 over the station
  and 200 over USB, the pushes still go out, plain http to the private
  address is refused (`untrusted`), https by name is taken; trusted again
  and the USB role as found.
- **t9** everything as found (one restart): the settings read back equal,
  the certificate sets as found, the Pi with no receiver, no name, no extra
  address and no files; no reset nobody asked for, no E line of
  `ha_webhooks`, `http_client_manager`, `wifi_manager` or
  `settings_manager`, the poster's stack headroom, the internal heap's
  floor.

Not covered here: a power cut while the settings are stored. Settings and
revision are one document of the Settings Manager, written to a temporary
file and renamed (its storage guarantees have their own target test), and a
torn pair could not lie anyway: the revision is reported only while the
fingerprint stored with it matches the settings in use.

## Records

- 2026-11-14 23:27 to 23:42, **end to end with the real integration**, by
  hand (the kit: `internal/ha_push_reply/`, on the Pi `~/pushreply` beside
  the Home Assistant kit `~/issue110`). The bench Home Assistant 2026.2.3 on
  rpi001 (plain http, `internal_url` `http://10.42.1.1:8123`), the unit
  below on the same build, the two trees of the ha-wican branch
  `meatpi-multi-device`: `27cd5b4` as it was and the working tree with the
  integration's side of this change.
  - **Reproduced with `27cd5b4`.** New connections from the Pi to the
    device's port 80 refused (an nftables rule: Home Assistant cannot call
    the device, the device's pushes still arrive, a car on LTE). The post
    interval changed from 7 to 15 in the entry's options: three registration
    attempts failed, `Failed to register webhook after 3 attempts`, the
    repair issue `webhook_registration_failed` raised; the pushes went on
    every 7 s. Rule removed: 40 s later the issue was still open, the device
    still on 7 s, no further registration.
  - **The working tree, the device reachable.** The first push after the
    start reported `''`: the reply carried the settings (`Webhook settings
    8dd85785 sent ... in the push reply (it reported '')`), the device took
    them (interval 15, `rev` `8dd85785`, `reply.applied` 1), the issue was
    gone. The diagnostics: reported and wanted revision equal.
  - **A car on LTE.** The rule again, the interval from 15 to 9: the
    registration failed, one I line (`Could not reach ... it is pushing, so
    its settings follow when it pushes`), NO repair issue; the next push got
    the settings (`5a98d0c3`, it reported `8dd85785`) and the pushes arrived
    every 9 s from then on, the rule still in place. Afterwards the device:
    interval 9, `rev` `5a98d0c3`, `reply.applied` 2, none ignored.
  - **A car asleep.** A blackhole route on the Pi (nothing passes) for 135
    s, so the device counted as away, then the interval from 9 to 12: the
    registration failed, the error line and the repair issue were there
    (right for a silent device). Route removed: the first push closed the
    issue and got the settings (`107b8708`), the device went to 12 s,
    `reply.applied` 3.
  - Both trees logged `homeassistant.core: Unable to remove unknown job
    listener ... _schedule_webhook_registration` once at a start with the
    entry in place (the zeroconf rediscovery reloads the entry while the
    started event is fired): older than this change, not fixed here.
  - Afterwards: `bench.sh restore` (the config dir identical to its backup,
    `ha-bench` inactive), the rule and the route gone, the unit's settings as
    found after one planned restart, `log_errors` 0, no fault.
- 2026-11-14 23:17, unit 14c19f457fd9 on the final build (`dev-v6` @
  `2e4b039` plus this change, components `73caa8b` plus this change):
  `.\test.ps1 pushreply -Untrusted`: **PUSH REPLY PASS**, every check of
  the legs above, 537 s, six planned restarts, 94 pushes at the receiver, no
  reset nobody asked for, no E line of the four tags, `ha_webhook` stack
  headroom 11,056 B (the 16 KB stack), the internal heap's floor 51,060 B.
  u0: the USB link answered at 192.168.82.1; u1: 403 over the station and
  200 over USB, plain http to the private address refused, https by name
  taken. Report `test-reports/2026-11-14_2317_pushreply.md`.
- 2026-11-14 23:45 to 23:58, the same build, the stages beside it:
  `.\test.ps1 release` **RELEASE TEST PASS** (385 s; its HA webhook leg
  read 14,612 B of headroom for `ha_webhook`) and `.\test.ps1 tlsbyip`
  **TLS BY IP PASS** (https by IP through the changed `http_client_manager`).
- 2026-11-14 23:08, the same unit on the build before the status builder
  was trimmed (the 16 KB stack already): `.\test.ps1 pushreply`: **PUSH
  REPLY PASS**, 431 s, three planned restarts, 78 pushes, stack headroom
  10,996 B, heap floor 51,080 B.
- 2026-11-14 23:02, the stack probe on the 8 KB stack (one boot per pair,
  `stack_hw` of `ha_webhook` from `/api/status/tasks`): idle 6388 B; after
  plain pushes 3316 B; after a reply's settings were stored 2644 B; a new
  boot, after https pushes 3076 B; after a reply stored over https 2996 B.
- 2026-11-14 22:47, the first run of the bench, on the first build of the
  change (the 8 KB stack): **PUSH REPLY PASS**, 31 checks plus the closing
  ones, three planned restarts, 117 pushes, stack headroom 2864 B, heap
  floor 51,048 B.
- 2026-11-14, the host suites: `.\test.ps1 host ha_webhooks` 9 tests,
  `.\test.ps1 host http_client_manager` 7 tests, both PASS. The web UI: the
  mock regenerated (`extract_mocks.py`, `make_preview.py`) and
  `node probe_automate.mjs` PASS with the new check (the Home Assistant card
  has no row for `rev` or `rev_sum`).
- 2026-11-14, the integration's side in its own harness (rpi002, the
  working tree on `27cd5b4`): five new tests that fail on `27cd5b4` for the
  two faults above and pass now; 834 passed, coverage 96.36 %, ruff 744 and
  mypy 1 as before, hassfest reports no invalid integration.

## Traps

- **The poster's stack was 8 KB, not 32.** The source said "8192 words =
  32 KB" since 2026-07; `StackType_t` is one byte on xtensa. The first run
  of this stage read 2864 B of headroom for `ha_webhook`, and a probe the
  same evening put numbers on it (one boot per pair, the 8 KB stack): 3316
  B after plain pushes, 2644 B after a reply's settings were stored, 3076 B
  after https pushes, 2996 B after a reply stored over https. The stack is
  16 KB since. A PSRAM stack overflows silently: the stage keeps the
  headroom as a metric and fails under 512 B.
- **The stats are per boot and a `DELETE` clears them.** `reply.applied`
  and `reply.ignored` are compared as differences, never as absolute
  numbers.
- **A push under way at the moment of a registration** reports the revision
  of before. The legs judge the pushes that follow a `settle`, not the first
  one after a change.
- **The bench's "public address" works only while the Pi is the DUT's
  gateway** (the bench AP bridges to `usb0`). On another rig the pushes to
  203.0.113.9 leave for the internet and the b1 and b3 legs fail with a
  connect error, not with a wrong verdict.
- **The name is mDNS.** `avahi-publish -a -R wican-pushreply.local
  203.0.113.9` needs `stdbuf` for its "Established" line to arrive over
  ssh, and the DUT resolves it because `CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES`
  is on. NetworkManager's dnsmasq on the Pi runs with `--no-hosts`: an entry
  in `/etc/hosts` is not served.
- **`-Untrusted` closes the station's way in.** The leg proves the USB link
  first and restores over it. If the PC loses the USB link in between, the
  DUT stays untrusted with its API closed over the station: reach it over
  its own access point (192.168.0.10) and set `sta_trusted` back there.
- `cert_set` is a setting (reboot-to-apply), the address is not: r1 selects
  the set before the https legs. A registration and a reply keep the stored
  `cert_set`.
