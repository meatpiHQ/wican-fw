# A drive, with or without an ESPNetLink

Command: `python tools\testbench\wifi\espnetlink_drive_bench.py --dut-ip <DutIp> [--drive-min 10] [--console auto|COMx|none] [--psu auto|none] [--only d2,d3,...] [--keep-log]`

Needs: PC + rpi001 (the P4 daemon up, `wican-bench-usb` active) + betty (ssh, the receiver on its port 8199) + the ECU sim v2 (the car on the bus and the unit's supply) + a card in the unit; the dongle on the connector for the uplink form

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** Ali, 2026-11-16: "test ESPNetLink scenarios: away from
home, came back home, sleep, wake up, drive". The garage story
([garage.md](garage.md)) proves the networks change hands; this bench
proves what the car does meanwhile. Prints `ESPNL DRIVE PASS` (or
`ESPNL DRIVE PARTIAL PASS (...)` with `--only` or a leg skipped for a rig
reason), about 20 min with the default 10 min drive.

The unit is taken as it is configured. With an uplink beside the station
(the dongle on the wire, or paired as a WiFi modem) the parameters must
keep arriving at a receiver on the internet from the carrier's side during
the drive. Without one (ESPNetLink off and the USB host off) nothing may
arrive, nothing may count as a failure, and the card holds the drive: the
store and forward case. Store and forward (the logger as JSON lines or CSV
with every AutoPID sample) is switched on for the run when the unit does
not have it; `--keep-log` leaves it on and the run's files on the card (a
Home Assistant pull follows).

Legs: d0 the rig (the station at home, AutoPID running and the car
answering, the P4's levers, the receiver, the card); d1 configure (a
destination `drive_pub` to the receiver every 5 s, store and forward,
sleep after 1 min; one planned restart); d2 the engine runs in the garage
(the supply at 14.0 V, the polls grow and none fails, the POSTs arrive
from home with the parameters, the rows reach the card; the poll rate
measured here is the drive's yardstick); d3 the drive (`ap off` for
`--drive-min` minutes: with an uplink a POST from the carrier's side
within 120 s and then POSTs all the way with no gap over 60 s, without one
no POST at all); d3r (uplink and console only) halfway, `espnetlink
repair` typed on the console, the POSTs must return within 150 s; d4 home
again (`ap on`: the unit back, a POST from home, MQTT back, no boot since
the garage, the polls and the recorded rows grew through the drive at the
garage's rate with nothing dropped, the destination's counters agree with
the receiver); d5 engine off (12.5 V: asleep within the delay, the settled
current reported); d6 the next start (14.0 V: awake, home, the uplink
back, the polls back, the wake a planned boot, and the drive read back
from the card through the export route from its first minute to its
last); d7 restore (the settings as found with one planned restart, the
run's log files deleted, the supply, the AP, the receiver; no reset nobody
asked for, no E line of the components involved, the heap's floor).

**How it observes.** At home the unit's HTTP API through an ssh tunnel to
the Pi. On the road nothing reaches the unit (no VPN here: the garage
bench has that), so the road is judged at the receiver on betty (every
POST: when, from which side, how many parameters), on the console when
there is one (reset and crash lines, the dongle repair typed there
through the tail's command file), and by the unit's own counters once it
is home again. Public addresses are never printed ("home" / "other").
The receiver keeps names and counts only: the fix is among the
parameters, so no value is stored. The console probe is `restart_tracker`
(the `espnetlink` command prints the position).

**Records.**

- 2026-11-16 00:32, 6.0.0-beta.11 (the release bytes) with the production
  dongle on 1.23.1 in `usb_rndis`
  (`test-reports/logs/beta11_fulltest_20261116/drive_rndis_run1.log`):
  33 PASS, 1 FAIL, the failing check the bench's own. Garage: 3.65 polls/s
  of 29 rows, none failed, 7.5 rows/s onto the card, 37 parameters per
  POST. The drive: the first POST from the road 15 s after `ap off`, 108
  POSTs in 10 min, every one whole, the longest gap 20.3 s, which is the
  dongle's power cycle typed halfway (52 POSTs behind it). Home 25.2 s
  after `ap on`; +2357 polls in 629 s, 0 failed; +4690 rows written, 0
  dropped; the destination success +113, fail +1. Asleep 64.7 s after
  12.5 V at 3.7 mA (the dongle's rail is off with the unit). The next
  start: home 9.1 s after the wake, the wire back at once. Heap floor
  40,188 B. The FAIL, `d6_the_drive_is_on_the_card`: the bench sent the
  cursor percent-encoded (`<epoch>%3A<offset>`) and read the same page 60
  times; see the traps.

- 2026-11-16 01:59, the same pair in `wifi_modem`
  (`drive_wifi_modem_run1.log`, the cursor sent bare): **`ESPNL DRIVE
  PASS`, 34 checks.** The first POST from the road 25.1 s after `ap off`
  (the station on the dongle's AP), 105 POSTs in 10 min with the fix in
  every one, the longest gap 16.9 s (the dongle's power cycle halfway);
  home 56.6 s after `ap on` (the 60 s look for the preferred network);
  +2489 polls, 0 failed; asleep at 3.6 mA; the drive read back from the
  card: 3808 rows of 36 parameters over 602 s in 9 pages, from the first
  second to the last. Heap floor 45,708 B.
- 2026-11-16 02:53, `espnetlink.enabled` false with the dongle still on
  the connector (`drive_espnl_off_wire.log`, `--drive-min 3 --only
  d2,d3,d4`): `ESPNL DRIVE PARTIAL PASS`. The bench read the uplink as
  `wire` (the USB host still enumerates the dongle as a network adapter)
  and the road agreed: 33 POSTs from the carrier's side in 3 min. Switching
  the link off does not take the dongle's uplink away.
- 2026-11-16 03:46, nothing to route through (`espnetlink.enabled` false,
  `usb_host_manager.enabled` false, no dongle AP among the station's
  networks), store and forward on as found, `--drive-min 16 --keep-log`,
  a Home Assistant 2026.11.1 with the integration at `1209458` watching
  (`b3_drive_no_uplink_ha.log`): **`ESPNL DRIVE PASS`, 33 checks.** 0 POSTs
  left the car in 16 min; the destination counted 175 offline laps and one
  failure; +3712 polls, 0 failed; home 25.3 s after `ap on`; asleep at
  3.6 mA; the drive on the card: 3734 rows of 30 parameters over 962 s.
  Home Assistant pulled 11 s after the first push (19 pages, the completed
  hour imported for 30 sensors, the running hour deferred and imported by
  the follow-up at 05:01:36); the card's rows of both hours against the
  recorder's statistics: mean, min and max equal for all 30 parameters.

**Traps.** The export route reads `since` raw: a cursor whose colon is
percent-encoded has no colon for the route, is taken for a plain time and
starts at the first row of that file again, with `more:true` for ever.
Send the cursor as it came (the web UI and the Home Assistant integration
do). The receiver's clock and the PC's are not compared: what came after
the repair is counted by position in the receiver's own list. A cold GPS
fix with the wire live is slow at the bench window: the fix in the POSTs
is reported, not judged. `test.ps1` must not be launched through a
wrapper on this PC (the antivirus deletes it); this bench is a python
script and may be started detached through `cmd /c`.
