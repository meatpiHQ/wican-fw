# Destinations and the Home Assistant webhook: active on

Command: `.\test.ps1 destnets` = `python tools/testbench/system/dest_networks_bench.py --dut localhost:18081 --pi rpi001 --pi-ip 10.42.1.1 [--observe 25] [--ignore-tag <tag>]` through the runner's tunnel

Needs: the DUT on the bench AP, rpi001 over ssh (the receiver `tools/testbench/actors/dd_receiver.py` is copied there and run as a transient systemd unit on port 8199, plain HTTP; a receiver already answering on 8199 is used as it is)

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

The `networks` setting of 2026-11-08 on both senders, against the station's
real network (`GET /api/wifi/status` `ssid`, `WICAN_TEST_AP` on the bench).
Four states, each a settings PUT + submit (reboot-to-apply), the settings
back as found at the end: five planned restarts.

- **dest-other**: one HTTP destination to the receiver, every 2 s, active on
  `NotThisNetwork`: the applied row reports its list, the status names the
  station's network, `on_network` false, nothing arrives in the observe
  window, the laps are counted as `skipped_network` (no failure, no
  success), the ring has `not on a selected network (<ssid>) - waiting`, a
  Test answers `not on a selected network`.
- **dest-this**: the same row active on the station's network: deliveries
  arrive, `on_network` true, success counted, no network skip, a Test
  delivers.
- **hook-other**: the Home Assistant webhook to the receiver every 2 s with
  `manual_override` on (the bench HA's registration cannot move the URL),
  active on another network: `GET /api/webhook` carries the list,
  `on_network` false, no push arrives, nothing counted as a failure, the
  ring has `not on a selected network: pushes wait`.
- **hook-this**: active on the station's network: pushes arrive,
  `on_network` true, success counted.
- the end: no new E line in the ring; the restore puts both documents back
  and reads them back equal.

## Records

- 2026-11-08 16:42 (unit 44bd8d73ca79 on `v6.0.0p-beta.7-14-g32d74a9-dirty`,
  the station on WICAN_TEST_AP): **DEST NETWORKS PASS**, 35 checks, about
  5 min, five planned restarts (`test-reports/2026-11-08_1642_destnets.md`;
  the console lines in `test-reports/logs/chip_prompt_2026-11-08/`).
  dest-other: `skipped_network` 15 in 25 s, 0 failures, 0 deliveries, the
  ring's line, Test refused with `not on a selected network`; dest-this: 16
  deliveries in 25 s, 16 successes, 0 network skips, Test delivered in
  16 ms; hook-other: 0 pushes, 0 failures, the ring's `pushes wait` line;
  hook-this: 16 pushes, 15 successes counted at the read; 0 new E lines; the
  restore read back equal.

## Traps

- The receiver is reached from the PC over ssh (`curl` on the Pi): the Pi's
  own address is not routable from the PC since it moved behind the router.
- The main destinations bench's receiver unit (`dd_receiver`) holds 8199
  while it runs: this bench then uses it instead of starting its own.
- A destination row restricted to a network that is not configured any more
  never sends: the UI lists such a name struck through in the dropdown.
