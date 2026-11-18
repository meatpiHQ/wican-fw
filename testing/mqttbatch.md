# CAN over MQTT with a small batch, and the losses on `/api/bridges`

Command: `.\test.ps1 mqttbatch` (= `python tools\testbench\can\mqtt_can_batch_bench.py --dut localhost:18081 --pi rpi001 --pcan <the PCAN channel on the DUT's bus> [--tiers 1@300x3,4@2000,24@2000] [--n 3000]`; the stage opens the tunnel through rpi001 for the web API).

Needs: PCAN on the DUT's bus (`WICAN_PCAN_DUT`) + rpi001 with mosquitto and its clients (the broker the unit's `mqtt_manager` already points at, which must be enabled). Prints `MQTT CAN BATCH PASS`, 19 checks, about 2 min, four planned restarts of the DUT (one per tier, one for the settings as found).

The index row and the other stages: [../TESTING.md](../TESTING.md). The first bench of this bridge (the gates, the legacy shape, the custom topics): `tools/testbench/can/mqtt_can_bench_test.py`.

## What it answers

Whether every MQTT message the unit publishes is one JSON document when `batch_frames` is small and the bus is busy, and whether a loss on this path can be read from the web API.

The bridge gathers what a translator emits during one pass of its pump into one send since 2026-11-17. With `batch_frames` 24 (the default) a batch is longer than what the pump gathers, so each send is one object. With a small batch the pump finishes several in one pass, and the `mqtt0` endpoint published them as they came: one MQTT message holding two or more objects back to back, which a JSON parser refuses (found 2026-11-18). The endpoint now publishes each object as a message of its own.

## What it covers

For each tier `<batch_frames>@<frames a second>[x<frames in a group>]` the bench switches `mqtt_can` on with both gates open, adds the row `br_mqtt_can = can <-canmqtt-> mqtt0`, restarts, and has the PCAN put exactly N frames of id 0x123 on the bus with a counter in the data. `mosquitto_sub` on the Pi keeps every message.

| Check | What is asserted |
|---|---|
| `the bridge is up and the broker connected` | the row runs, `mqtt_connected` |
| `A1 every MQTT message is one JSON object` | every payload parses as exactly one object, nothing behind it |
| `A2 every frame arrived once and in order, or is in a counter of a route` | the counters of the frames in the messages are 0 to N-1 once each, in order; a frame that is missing must be covered by loss counters of this path that moved (`/api/bridges` for `br_mqtt_can`, `/api/can`) |
| `A3 no loss counter of a route moved without a loss` | with nothing lost, no loss counter of this path moved |
| `T1 both frames of the tx message on the bus, byte exact` | the other direction still works: one tx message with two frames, read back on the PCAN |
| `C1 the unit counted the message (console: tx-oversize)` | one tx message of 1,855 bytes, longer than the reassembler takes (1,024): the console command `mqtt_can` counts it |
| `C2 /api/bridges shows the same count` | `losses.b.tx_oversize` of `br_mqtt_can` moved by the same one. The `mqtt0` end reports `dropped_rx_gate`, `dropped_tx_gate`, `dropped_q_full`, `parse_errors`, `tx_cut`, `tx_oversize`, `dropped_rx_frames`, `pub_refused`, and the MQTT client's own `mqtt_dropped_full` and `mqtt_dropped_offline` |
| `C3 every running bridge reports what the jacks at its two ends lost` | every row with `up` true has `losses.a` and `losses.b` |
| `R1` to `R3` | settings back as found, only the planned restarts, no new E line |

The first tier, `1@300x3`, is the one that shows the defect every time: one frame a message, the frames in groups of three back to back, so three frames reach the unit as one chunk and the pump runs the codec on all three in one pass.

## What it does not cover

TLS brokers, QoS 1, a broker that is slow or away for long (the `pub_refused` counter is asserted only when a frame is missing). The loss counters of the other jacks (`can`, `ble`, `cli`, `usb_obd`) are checked to be present, not provoked: see [usbobd.md](usbobd.md) for `usb_obd`.

## Records

**2026-11-18 11:55, bench unit 14c19f457fd9, the firmware before the fix (image elf `7bb648422357a1e2`), PCAN channel 2: `MQTT CAN BATCH FAIL`.** The tiers of that first run were `4@2000,1@1000,24@2000` with 6,000 frames each.

| Tier | Messages | With several objects | Frames arrived | Loss counters that moved |
|---|---|---|---|---|
| 4 frames a batch, 2,000 frames/s | 1,522 | 1 | 6,000 of 6,000 | none of this path |
| 1 frame a batch, 1,000 frames/s | 4,878 | 115 | 5,006 of 6,000 | `br_mqtt_can` `send_errors` +928 (the broker queue was full: 1,000 publishes a second is above what the path carries) |
| 24 frames a batch, 2,000 frames/s | 314 | 0 | 6,000 of 6,000 | none of this path |

`C1` passed (the console counted the oversize message, 0 to 1) and `C2` failed: `/api/bridges` had no number for it.

**2026-11-18 13:58 and 14:03, the same unit, the final build of the day (image elf `2d790852dd98d8ae`): `MQTT CAN BATCH PASS` twice, 19 checks, 122 s and 123 s.** 3,000 frames a tier.

| Tier | Messages (first run, second run) | With several objects | Frames arrived | Loss counters that moved |
|---|---|---|---|---|
| 1 frame a batch, 300 frames/s in groups of 3 | 2,927 and 2,948 | 0 | 3,000 of 3,000 | none |
| 4 frames a batch, 2,000 frames/s | 779 and 780 | 0 | 3,000 of 3,000 | none |
| 24 frames a batch, 2,000 frames/s | 166 and 170 | 0 | 3,000 of 3,000 | none |

`C2`: `losses.b.tx_oversize` 0 to 1 beside the console's count. `C3`: six running bridges, every one with `losses.a` and `losses.b`.

**2026-11-18 17:48, the same unit, the merged build of that evening (image elf `29c018a9067b831d`): `MQTT CAN BATCH PASS`, 19 checks, 142 s.** 2,939, 778 and 171 messages in the three tiers, none with several objects, 3,000 of 3,000 frames in each, no loss counter moved.

Two runs before it (17:06 and 17:28, image elf `df9dcea581095b8c`) failed `A2` in all three tiers the same way: 2,042 and 2,049 of 3,000 frames in the first tier, the first 950 missing, and none of 3,000 in the other two, while the unit counted every frame batched and no loss counter moved. The bench, not the unit: the subscriber on the Pi came up late. An ssh to the Pi took 7.4 s to begin that evening (the PC's lookup of the Pi's name took 7.2 s, then 2.7 s, then nothing for ten seconds, in a cycle; ssh by address took 0.06 s), the bench assumed the subscriber stood 2.5 s after its start, and the first three seconds of frames went by before it did. The bench now waits for the subscriber's own word. The run of 13:50 below, a tier that delivered nothing, fits the same cause.

Two runs on builds of the same day before the final one, kept because they did not pass:

- 13:08 (image elf `01aafe2f17843f60`): the first tier came 6 frames short at the collector. The collector's window ended before the tier's last frames (the bench then waited `n / rate + 6` s for a tier of 10 s); it is `n / rate + 14` s now.
- 13:50 (image elf `bafe0402df0c7399`): the tier of 4 frames a batch delivered none of its 3,000 frames to the collector, while the unit counted them batched (`frames rx` +3,068) and no loss counter of any route moved. Not seen again in the two runs above or in the run of 13:08. The MQTT client's own two drop counters were on no route at that time (they are on the `mqtt0` end since the final build), so where those messages went is not known. If it happens again, `mqtt_dropped_offline` is the first number to read.

## Traps

- The unit must already have `mqtt_manager` enabled and pointed at the Pi's broker; the bench does not touch those settings and stops at once when the manager is off.
- A tier above about 500 messages a second (one frame a message at 1,000 frames a second) is more than the unit's MQTT queue carries: messages are refused and counted (`pub_refused`, and the bridge's `send_errors`). That is a limit of the path, not the defect; the default tiers stay below it.
- The subscriber must stand before the first frame, and an ssh to the Pi does not begin at once: the bench starts `mosquitto_sub` behind an `echo SUBSCRIBER_UP`, waits for that line and sends the first frame 1.5 s after it. Until 2026-11-18 it sent the first frame a fixed 2.5 s after starting the ssh, and a slow start of the ssh cost the first seconds of frames, which `A2` counts lost.
- The first request of the bench waits up to 40 s for an answer: the stage's tunnel may not stand yet for the same reason.
- The bridge row of another jack counts its own traffic meanwhile (`br_ble_obd` counts a send error for every chunk of the OBD chip while no phone is connected): the bench looks only at this path's counters.
