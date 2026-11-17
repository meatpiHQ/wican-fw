# A script's raw CAN functions and its CAN_TX pulse

Command: `python tools/testbench/can/script_can_bench.py [--dut localhost:18081] [--pcan PCAN_USBBUS1]`

Needs: main fw on DUT through rpi001 (the tunnel of port 80); PCAN on the DUT's bus with python-can on this PC (`WICAN_PCAN_DUT` names the channel when the adapter's plugs are swapped)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** The five script functions that came with meatpi-components
pull request 2 (Elias Kotlyar): `can_rx`, `can_start`, `can_stop`,
`digitalWrite` and `digitalPWM`. The scripts run inline through
`POST /api/scripts/run`; PCAN is the witness on the bus and the generator.
The bench unit keeps the script engine and the `/ws/can` channel off and has
no bridge on the CAN jack: the three are staged for the run (one planned
restart) and put back at the end (one more), also when a leg dies.

Legs: c1 `can_rx` hands the script a frame PCAN puts on the bus (the data,
`can_rx_id`, `can_rx_dlc`); c2 `can_rx` on an id nobody sends answers `nil`
when its time is up; c3 the pin rule: a board pin (GPIO 15, the OBD chip's
UART) is refused with the bus stopped, `CAN_RX` is refused (the transceiver's
output), `CAN_TX` is refused while the bus runs, `digitalPWM` follows the same
rule, and the bus runs after every refusal; c4 `can_stop()`, ten dominant
pulses of 1.6 ms on `CAN_TX`, `can_start()`: the script ends well, PCAN saw
bus errors while it ran, the controller runs and is not bus-off; c5 after the
pulse a frame from the script reaches PCAN and a frame from PCAN reaches the
script; c6 a script that stops the bus and ends without `can_start()`: the
bus runs again by itself and the next script transmits; c7 the CAN monitor's
bridge (`can <-slcan-> ws_can`) still receives 100 of 100 frames after all
those stops (the jack joins the bus again by itself); c8 the settings back as
found, planned restarts only, no E line of `script_engine`, `script_gpio`,
`can_manager`, `can_core`, `bridge_endpoints`, `bridge_manager`.

**Traps.**

- The pulse leg disturbs the bench bus on purpose: PCAN counts about a
  thousand bus errors for the ten pulses and python-can prints `Bus error: an
  error counter reached the 'heavy'/'warning' limit`. The adapter recovers
  with the next frames; the legs behind it prove the bus works.
- PCAN's error messages come in two sorts; only those with an id other than 0
  are errors on the bus (the others are notes that its counters moved). The
  bench counts the first sort.
- The stop path the script takes (`can_manager_stop`) is the one the device
  takes into sleep. A sleep and a wake on the same image are in the Release
  Test (r12w).

**Records.**

- 2026-11-18 00:30, the bench unit on the work build of that night (pull
  request 2 merged, ours on top, image `c9c1c261...`): **`SCRIPT CAN PASS`**,
  every leg, two planned restarts, no unexpected reset, no own E line. c1
  `11 22 33`, id 1697, dlc 3 in 0.7 s; c2 `nil` after 0.47 s; c4 the script
  answered in 0.08 s and PCAN counted 983 bus errors, bus-off 0; c7 100 of
  100. `httpd` headroom 7216 B, internal heap floor 43,028 B. Two runs on
  earlier builds of the same night passed the same legs.
- The run before the first green one died in c7 on the bench's own gap: the
  `/ws/can` channel is off on a unit with the canonical bench settings (a 404
  at the WebSocket handshake). The bench stages the channel since.
