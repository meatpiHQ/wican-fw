# Bluetooth Lean + Tailscale + the webhook poster, 30 minutes

Command: `python tools/testbench/vpn/ble_ts_soak.py --snapshot DIR --out DIR [--minutes 30]`
(PC; the DUT through rpi001, the simulator on its USB-NCM link, betty over ssh)

Needs: betty provisioned for `ts_public_soak.sh` + rpi001 + the ECU simulator's
`ble_central_bench` component + the DUT in station mode on the bench AP with an internet
path; `--snapshot` is a folder of the DUT's settings documents (`<component>.json`, what
`GET /api/settings/<component>` answers) the close compares against and restores to.

The index row and the other stages: [../TESTING.md](../TESTING.md).

**What it is.** The acceptance scenario of `internal/TASK_ble_tailscale_margin.md` section 3
(the field scenario of issue 1024): Bluetooth on the Lean profile with a central connected
and streaming, Tailscale up with its relay and a peer, the Home Assistant webhook poster
posting every 15 s, for 30 minutes, the lowest internal-RAM point recorded. Built 2026-11-01
once levers 2 (the Tailscale stacks on PSRAM) and 3 (the NimBLE host task on PSRAM) were in.
Phases: (0) the found `ble_manager`, `interface_manager` and `ha_webhooks` documents saved;
(1) the DUT staged: Bluetooth on, Lean, passkey 421337, `sta_ble_handover` off (the station
and the tunnel stay up beside the link; on the rig without the dongle Tailscale runs over the
station), the webhook poster at `http://10.99.0.1:8199/webhook` (betty's `dd_receiver.py`
through the tailnet's subnet route), one reboot; (2) `ts_public_soak.sh up` (headscale, the
betty peer, the DUT's join and its legs; it reboots the DUT once more for the VPN settings,
so the soak's boot baseline is taken after it); (3) the simulator's central configured
(`ble_central_bench` enabled, 1M, the passkey; a simulator reboot when changed), connected
and secured; (4) the soak: the central writes to the DUT's data pipe in 60 s legs back to
back (about 250 kbps), a sample every 10 s of `/api/status` (free, min free, largest block,
`ble_connected`, `log_errors`, `unexpected_resets`, `boot_count`), `/api/vpn` (state, relay,
peers), `/api/webhook` (success and fail counts) and the central's state, to `samples.csv`;
(5) the checks: the lowest point (min free since the boot that joined, and the lowest
sample) at least 25 KB (the proposal of section 3), no unexpected reset and no reboot, no BLE
link drop, the tunnel connected throughout and the relay up at the end, the webhook posts
grew by at least two per minute with no new failure, betty's receiver holds them (WARN), no
E line of the feature's tags (the HTTP client's connect triplet of a post made before the
route existed is reported as a WARN), no `lwip_guard` refusal, no pairing failure; (6) the
teardown: the central disconnected and the simulator's settings back, `ts_public_soak.sh
down` (the keys wiped, the tsroute row gone), the three documents back with one reboot, and
every document of the snapshot compared (`p6_found_documents_back`).

**2026-11-01 02:43, the first run (the lever 3 image, the rig without the dongle: the DUT's
USB-C on the PC in the device role): every functional check green, the verdict line FAIL on
the bench's own baseline.** With Bluetooth Lean on and no tunnel: 63.3 KB internal free. With
Tailscale up (the relay up, the subnet route learned, the `up` legs all PASS): 57.5 KB.
Over the 30 minutes with the central streaming at 247 to 253 kbps in 27 write legs and the
webhook posting every 15 s (8 → 130 successes, 122 posts, the 3 failures all before the
start: posts made before the route existed; betty's receiver 9 → 131 records): **free
internal flat at 57,503 B** (79 samples: median and maximum 57,503, the lowest 53,031 B,
largest block 31,744 B throughout), min free since the boot that joined 39,740 B (the boot's
own floor, set before Bluetooth and the tunnel came up: the same number `bleram` reads for
the boot), 0 unexpected resets, 0 BLE link drops (`ble_connected` true in every sample), the
tunnel connected in every sample and the relay up at the end, 0 `lwip_guard` refusals, pair
ok/fail 1/0, 0 E lines of the feature's tags (`health.log_errors` 9: three connect triplets
of the HTTP client, `esp-tls` / `transport_base` / `HTTP_CLIENT`, from the webhook and the
tsroute deliveries attempted before the route existed at the join's boot, the pre-existing
logging `testing/tspublic.md` describes). The teardown clean: `down` rc 0, the three
documents back, 44 documents compared with the session's snapshot, none differs. The FAIL:
`p5_no_reboot_during_the_soak (boot 4 vs 3)`: the baseline was taken before `up`, which
reboots the DUT; every sample read boot 4. Fixed in the bench (the baseline after `up`), the
second run below. Against the numbers of the night before (`TASK_ble_tailscale_margin.md`
section 2: Bluetooth Lean + Tailscale on the flash-only build with the dongle, 18.6 KB steady
and 15.2 KB lowest over 3 minutes): about 57 KB steady without the dongle, roughly 50 with
it (its enumeration costs about 7.7 KB): levers 2 and 3 together are worth about 30 KB in
this scenario. Folder `test-reports/logs/ble_ts_soak_20261101_0243/` (`run.log`,
`samples.csv`, `ts_up.log`, `ts_down.log`, the found documents).

**2026-11-01 03:27, the second run (the final image of the night, the bench's boot baseline
taken after `up`): `BLE TS SOAK PASS`, every check green, no WARN.** Bluetooth Lean on, no
tunnel: 63.4 KB free; right after the join 53.1 KB (the flash worker's 4.4 KB alive for 5 s
after the join's NVS work); over the 30 minutes **free internal 57,491 B median, 57,523 B
maximum, 53,019 B the lowest sample** (78 samples; largest block 31,744 B throughout), the
central streaming in 26 write legs, the webhook 8 → 129 successes (121 posts, 0 new
failures; betty's receiver 9 → 130), min free since the boot that joined 39,740 B (the boot's
floor), boot 8 in every sample, 0 unexpected resets, 0 BLE link drops, the tunnel connected
in every sample and the relay up at the end, 0 `lwip_guard` refusals, pair ok/fail 1/0, 0 E
lines of the feature's tags (`health.log_errors` 9, the HTTP client's connect triplets of
the posts before the route existed; the ring at the end held none of them). Teardown clean,
44 documents compared, none differs. Folder `test-reports/logs/ble_ts_soak_20261101_0325/`.
