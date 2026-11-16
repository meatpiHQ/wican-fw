# A rule's http.post, plain and over https

Command: `.\test.ps1 rulepost -DeviceId <the bench unit>` = `python tools/testbench/system/rule_http_post_bench.py --dut localhost:18081 --pi rpi001 --pi-ip 10.42.1.1 [--https-url URL ...] [--headroom 4096]` through the runner's tunnel

Needs: the DUT on the bench AP with an internet path (DNS and 443 out), its MQTT client on the Pi's broker (the bench stages `enabled` and `url` when it is not), rpi001 over ssh (`mosquitto_pub` and `python3` there; the bench runs a plain HTTP receiver on port 8208 and removes it at the end)

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The action `http.post` of the rules engine (Automate, Rules) on the two
tasks it runs on. A rule's action that may block (`http.post`,
`obd.request`, `uds.request`) is handed to a pool of two workers
(`em_worker`), and `http_client_manager` runs the TLS handshake of an
`https://` request on the stack of the task that calls it. Until 2026-11-16
a worker had 4 KB: every https post overflowed it and the device reset with
`***ERROR*** A stack overflow in task em_worker has been detected.`
(GitHub issue 1067, reported on 6.0.0-beta.10 with a rule posting bumps to
a Home Assistant webhook). A plain `http://` post fitted (2.7 KB), so did a
post that failed before the handshake (no network, no DNS), and no bench
had ever fired `http.post` at an https URL: the stage `live`'s
`em_worker_bench.py` posts to a plain receiver. The workers have 16 KB now
(`EVENT_MANAGER_WORKER_STACK`), and the build refuses a worker stack below
the HTTP client's caller budget (`_Static_assert` in
`http_client_manager_events.c`).

The trigger is `mqtt.rx`: one message from the Pi's broker fires a rule
once, on demand. A timer would fire at every boot, and an image with the
defect would crash three times in a row and park.

The legs:

- **p0** as found: the rules, the MQTT settings, the two workers' headroom.
- **p1** the rules stored (restart 1: rules are a setting), the unit's MQTT
  on the Pi's broker, the receiver started. Six rules, all `http.post` with
  the body of the report (`{"state":"bump","axes":"${payload}","ts":"${time.iso}"}`):
  one to the receiver, one per https URL, two more on the first https URL
  for p4.
- **p2** plain http: one message; the receiver has the rendered body, the
  rule fired, no restart.
- **p3** https, one URL after the other, one message each. The defaults:
  `developers.cloudflare.com` (an ECDSA chain under Google Trust Services,
  the host of the stage `tlschain`) and `valid-isrgrootx1.letsencrypt.org`
  (an RSA chain, Let's Encrypt's own test host). Both refuse the POST with
  405, which is all the leg needs: the handshake went through and the
  server answered. The check passes when the rule fired or the action
  failed on the HTTP status (`action http.post failed (ESP_FAIL)`), and
  fails on a transport error or a restart.
- **p4** two https posts 0.3 s apart: the second runs on the other worker
  while the first is in its handshake. Both ran, both workers have run a
  handshake (their headroom fell by more than 2 KB under the idle figure),
  each keeps `--headroom` bytes (4096).
- **p5** as found (restart 2): the rules and the two MQTT keys back, no
  restart nobody asked for, no E line of `event_manager` or
  `http_client_manager`, the stack headroom of `em_worker` and
  `em_dispatch`, the internal heap's floor.

On an image with the defect the stage stops at its first https post: `FAIL:
p3 https ...: no restart`, then the crash note of the boot (`boot N: reset
reason 'panic': abort: ***ERROR*** A stack overflow in task em_worker ...`),
and it still puts the rules back. One crash a run, so the unit does not
park.

## Records

- 2026-11-16 11:55, the same unit on the image `c7a7c0bf15dbce03` (the fix
  plus the data destinations poster's stack at 32 KB, [abrp.md](abrp.md)):
  **RULE POST PASS**, 82 s, two planned restarts, the workers 11,908 B, the
  internal heap's floor 43,488 B. Report
  `test-reports/2026-11-16_1155_rulepost.md`.
- 2026-11-16 11:28, unit 14c19f457fd9, the final image of the fix
  (`fb90b8bb787b5a3c`, the same sources as below with the comments
  finished): **RULE POST PASS**, 15 checks, 83 s, two planned restarts; the
  workers 11,908 B and 12,084 B after the pair, `em_dispatch` 5,188 B, the
  internal heap's floor 43,468 B. Report
  `test-reports/2026-11-16_1128_rulepost.md`. The same hour on that image:
  `.\test.ps1 host event_manager` 28 tests, `host http_client_manager` 7
  tests, `.\test.ps1 stackaudit` PASS (the static half reads `em_worker`
  as `16384 PSRAM`). At 11:32 once more with the bench's clean-up put
  right (the last trap below) and a marker of the run in the message:
  **RULE POST PASS**, the receiver had `"axes":"rp789212"`, nothing left on
  the Pi. Report `test-reports/2026-11-16_1132_rulepost.md`.
- 2026-11-16 11:13, unit 14c19f457fd9, the image with the fix
  (`d5872c15ae052d4f`, dev-v6 `0bfaf19` with the uncommitted trees of that
  morning): **RULE POST PASS**, 15 checks, 83 s, two planned restarts. The
  workers' headroom: 15,268 B idle, 13,684 B after the plain post, 11,908 B
  after an ECDSA handshake, 12,116 B after an RSA one, 11,908 B on both
  after the pair; `em_dispatch` 5,188 B; the internal heap's floor
  43,544 B. Report `test-reports/2026-11-16_1113_rulepost.md`.
- 2026-11-16 11:19, the same unit on the published 6.0.0-beta.11
  (`f9a7494f5245e7a8`, put on through `POST /api/ota/upload`): **RULE POST
  FAIL**, as it must: p2 passed with 1,396 B left of the 4 KB, p3 `no
  restart` failed, `boot 3: reset reason 'panic': abort: ***ERROR*** A stack
  overflow in task em_worker has been detected., core 0, task "em_worker",
  up 29 s`, the rules back as found. The console of the run has the panic
  (`test-reports/logs/2026-11-16_1119_rulepost/console_rule_post.log`).
  Report `test-reports/2026-11-16_1119_rulepost.md`.
- 2026-11-16 10:57 to 11:10, the reproduction and the measurement by hand
  (`internal/issue_1067/tools/repro_1067.py`, the same rules on topics of
  its own): on the published 6.0.0-beta.11 the plain post ran and the first
  https post reset the unit (**REPRODUCED**, the abort text and the
  backtrace of the report, which decodes to `vTaskSwitchContext` ->
  `vApplicationStackOverflowHook`); on the image with 16 KB workers the same
  run and a second one over four hosts ended without a restart. A worker's
  stack use, from its high-water mark: 1,116 B idle, 2,700 B for a plain
  post, 4,300 B for `valid-isrgrootx1.letsencrypt.org` (RSA), 4,476 B for
  `developers.cloudflare.com`, `valid-isrgrootx2.letsencrypt.org` and
  `postman-echo.com` (ECDSA; the last one answers 200, so the rule counts
  as fired).

## Traps

- `test.ps1` resolves the DUT by its device id and the default is not the
  bench unit of today: without `-DeviceId` the preflight says `dut=OFFLINE`
  at another address and the bench ends in `BENCH ERROR: GET /api/status`
  (the first run of 2026-11-16).
- The two default hosts answer 405 to a POST, so each https post leaves one
  W line (`event_manager: action http.post failed (ESP_FAIL)`) and counts in
  `stats.action_errors`, not in the rule's `fired`. That is the pass case.
  `ESP_FAIL` is the action's word for a status outside 2xx; a transport
  failure reads `ESP_ERR_HTTP_CONNECT` (or another name) and fails p3.
- The hosts are public and not ours. If one stops answering, check it from a
  PC first (`curl -X POST -d x https://<host>/`), then pass others with
  `--https-url`; keep one ECDSA and one RSA chain (`openssl s_client
  -connect <host>:443` names the key types).
- A worker's `stack_hw` is a low-water mark since the boot, and the two
  tasks carry the same name: the bench reads both rows and sorts them. Which
  worker takes a job is FreeRTOS's choice (the one that waited longest), so
  p3 alone may leave one worker without a handshake; p4 is what puts one on
  each.
- The unit's MQTT must reach the Pi's broker for the trigger. The bench
  stages `mqtt_manager.enabled` and `url` only (never the whole document)
  and puts those two keys back.
- A PSRAM stack overflows without the immediate panic of an internal one
  (the standard, section 2). Here FreeRTOS's check of the stack's last bytes
  at the task switch caught it, after the bytes below the stack were written:
  the two workers' stacks are rows of one array, so the second worker
  overflows into the first one's stack, and the first into whatever the
  linker put under the array.
- `pkill -f <the receiver's file>` sent over ssh also matches the shell
  that runs it, which then dies before the `rm` behind it: the first runs
  of 2026-11-16 left the receiver's script and log in the Pi's `/tmp`, and
  a log left over could have answered p2's look at the body. The bench
  kills with a bracketed pattern (`[r]ule_post_recv.py`), removes the files
  in a command of its own, and p2 looks for a marker of this run
  (`"axes":"rp<number>"`).
